"""Optional loopback bridge from a provider runtime to CERBERUS decisions.

When ``CERBERUS_TICK_URL`` is unset, callers keep using their in-process
decision function.  Once configured, the bridge is an isolation boundary:
only a sanitized snapshot is sent, and any configuration, transport, or
contract failure produces a safe rest action instead of falling back to the
local core.
"""

from __future__ import annotations

import ipaddress
import json
import math
import os
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

import requests

from claw_contract import (
    CHAT_MAX_CHARS,
    FREE_ACTIONS,
    KNOWN_ACTION_TYPES,
    REQUIRED_ACTION_FIELDS,
    THOUGHT_MAX_CHARS,
)


BRIDGE_URL_ENV = "CERBERUS_TICK_URL"
BRIDGE_TOKEN_ENV = "CERBERUS_HTTP_TOKEN"
BRIDGE_TIMEOUT_SECONDS = 2.0
MAX_REQUEST_BYTES = 1_000_000
MAX_RESPONSE_BYTES = 128_000
MAX_IDENTIFIER_CHARS = 256
MAX_STATE_DEPTH = 24

_CREDENTIAL_KEY_MARKERS = (
    "apikey",
    "authorization",
    "bearertoken",
    "clientsecret",
    "cookie",
    "credential",
    "mnemonic",
    "password",
    "passphrase",
    "privatekey",
    "recoveryphrase",
    "refreshtoken",
    "secret",
    "seedphrase",
    "sessiontoken",
)
_CREDENTIAL_KEY_SUFFIXES = (
    "auth",
    "seed",
    "token",
)
_ACTION_STRING_LIMITS = {
    "targetId": MAX_IDENTIFIER_CHARS,
    "targetType": 32,
    "regionId": MAX_IDENTIFIER_CHARS,
    "itemId": MAX_IDENTIFIER_CHARS,
    "message": CHAT_MAX_CHARS,
    "reason": THOUGHT_MAX_CHARS,
    "thought": THOUGHT_MAX_CHARS,
}
_ACTION_FIELDS = {"type", *_ACTION_STRING_LIMITS}
_CLOSED_ACTION = {
    "type": "rest",
    "reason": "CERBERUS decision bridge unavailable; no remote action accepted",
}


class DecisionBridgeError(RuntimeError):
    """A deliberately credential-free bridge failure."""


def _normalized_key(value: object) -> str:
    return "".join(character for character in str(value).lower() if character.isalnum())


def _is_credential_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return any(marker in normalized for marker in _CREDENTIAL_KEY_MARKERS) or any(
        normalized == suffix or normalized.endswith(suffix) for suffix in _CREDENTIAL_KEY_SUFFIXES
    )


def _sanitize_value(value: Any, *, depth: int, seen: set[int]) -> Any:
    if depth > MAX_STATE_DEPTH:
        raise DecisionBridgeError("snapshot nesting exceeds bridge limit")
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise DecisionBridgeError("snapshot contains a non-finite number")
        return value
    if isinstance(value, dict):
        identity = id(value)
        if identity in seen:
            raise DecisionBridgeError("snapshot contains a cycle")
        seen.add(identity)
        try:
            return {
                str(key): _sanitize_value(item, depth=depth + 1, seen=seen)
                for key, item in value.items()
                if not _is_credential_key(key)
            }
        finally:
            seen.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in seen:
            raise DecisionBridgeError("snapshot contains a cycle")
        seen.add(identity)
        try:
            return [_sanitize_value(item, depth=depth + 1, seen=seen) for item in value]
        finally:
            seen.remove(identity)
    raise DecisionBridgeError("snapshot contains an unsupported value")


def sanitize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe snapshot with credential-bearing fields removed."""

    if not isinstance(state, dict):
        raise DecisionBridgeError("snapshot must be an object")
    sanitized = _sanitize_value(state, depth=0, seen=set())
    encoded = json.dumps(sanitized, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise DecisionBridgeError("snapshot exceeds bridge size limit")
    return sanitized


def validate_tick_url(value: str) -> str:
    """Accept only an explicit HTTP(S) loopback URL ending at ``/tick``."""

    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise DecisionBridgeError("bridge URL must use HTTP(S)")
    if parsed.username or parsed.password:
        raise DecisionBridgeError("bridge URL must not contain credentials")
    if parsed.params or parsed.query or parsed.fragment or parsed.path != "/tick":
        raise DecisionBridgeError("bridge URL must target /tick")
    host = (parsed.hostname or "").lower()
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise DecisionBridgeError("bridge URL must use a loopback host")
        except ValueError as exc:
            raise DecisionBridgeError("bridge URL must use a loopback host") from exc
    try:
        parsed.port
    except ValueError as exc:
        raise DecisionBridgeError("bridge URL has an invalid port") from exc
    return value


def _validated_text(action: dict[str, Any], field: str, *, required: bool = False) -> str | None:
    value = action.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise DecisionBridgeError(f"action field {field} must be a non-empty string")
    if len(value) > _ACTION_STRING_LIMITS[field]:
        raise DecisionBridgeError(f"action field {field} exceeds its limit")
    if field in {"targetId", "targetType", "regionId", "itemId"} and not value.isprintable():
        raise DecisionBridgeError(f"action field {field} contains control characters")
    return value


def validate_action_object(value: Any, *, allow_side_effects: bool = True) -> dict[str, Any]:
    """Validate and narrow an action received across the bridge boundary."""

    if not isinstance(value, dict):
        raise DecisionBridgeError("action must be an object")
    action_type = value.get("type")
    if not isinstance(action_type, str) or action_type not in KNOWN_ACTION_TYPES:
        raise DecisionBridgeError("action type is not supported")
    unexpected = {key for key in value if not str(key).startswith("_") and key not in _ACTION_FIELDS}
    if unexpected:
        raise DecisionBridgeError("action contains unsupported fields")

    action: dict[str, Any] = {"type": action_type}
    required_fields = set(REQUIRED_ACTION_FIELDS.get(action_type, ()))
    for field in _ACTION_STRING_LIMITS:
        text = _validated_text(value, field, required=field in required_fields)
        if text is not None:
            action[field] = text

    effects = value.get("_side_effects")
    if allow_side_effects and effects is not None:
        if not isinstance(effects, list):
            raise DecisionBridgeError("action side effects must be a list")
        safe_effects: list[dict[str, Any]] = []
        for effect in effects:
            if not isinstance(effect, dict) or effect.get("type") != "game_free_action":
                continue
            free_action = validate_action_object(effect.get("action"), allow_side_effects=False)
            if free_action["type"] not in FREE_ACTIONS:
                raise DecisionBridgeError("bridged side effect is not a free action")
            safe_effect: dict[str, Any] = {"type": "game_free_action", "action": free_action}
            reason = effect.get("reason")
            if reason is not None:
                if not isinstance(reason, str) or len(reason) > THOUGHT_MAX_CHARS:
                    raise DecisionBridgeError("side-effect reason is invalid")
                safe_effect["reason"] = reason
            safe_effects.append(safe_effect)
            if len(safe_effects) == 2:
                break
        if safe_effects:
            action["_side_effects"] = safe_effects
    return action


def request_remote_action(state: dict[str, Any], *, url: str, token: str) -> dict[str, Any]:
    """Send one sanitized snapshot and return one validated action."""

    endpoint = validate_tick_url(url)
    if not token:
        raise DecisionBridgeError("bridge bearer token is required")
    sanitized = sanitize_state(state)
    session = requests.Session()
    session.trust_env = False
    try:
        response = session.post(
            endpoint,
            json={"state": sanitized},
            headers={"Authorization": f"Bearer {token}"},
            timeout=BRIDGE_TIMEOUT_SECONDS,
            allow_redirects=False,
        )
        if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
            raise DecisionBridgeError("bridge response was not accepted")
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise DecisionBridgeError("bridge response contract was not accepted")
        return validate_action_object(payload.get("action"))
    except DecisionBridgeError:
        raise
    except Exception:
        raise DecisionBridgeError("bridge request failed") from None
    finally:
        session.close()


def decide_action(
    state: dict[str, Any],
    *,
    in_process: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Choose the configured remote bridge or preserve the legacy local path."""

    url = os.getenv(BRIDGE_URL_ENV, "").strip()
    if not url:
        if in_process is None:
            from core_loop import cerberus_tick

            in_process = cerberus_tick
        return in_process(state)
    token = os.getenv(BRIDGE_TOKEN_ENV, "").strip()
    try:
        return request_remote_action(state, url=url, token=token)
    except DecisionBridgeError:
        return dict(_CLOSED_ACTION)
