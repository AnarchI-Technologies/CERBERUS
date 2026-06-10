"""Always-on Claw Royale gameplay connector for Render.

The dashboard and /tick endpoint are only the shell. This worker owns the live
WebSocket loop: join/listen, pass snapshots through the deterministic cortex,
send actions back, and persist compact runtime status for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import websockets

from claw_contract import JOIN_DECISIONS, THOUGHT_MAX_CHARS
from claw_config import CLAW_API_BASE, active_claw_version, claw_api_base, reconcile_claw_version
from claw_signing import ClawSigningError, sign_typed_data_frame
from core_loop import cerberus_tick
from env_loader import hydrate_env
from onboarding_clients import ClawRoyaleClient
from runtime_state import (
    claw_runtime_status_file,
    read_json,
    remember_game_id,
    stored_game_id,
    update_claw_runtime_status,
    write_json,
)


DEFAULT_MIN_RECONNECT_SECONDS = 5
DEFAULT_MAX_RECONNECT_SECONDS = 90
DEFAULT_ROUTE_PROBE_SECONDS = 15
JOIN_PATH = "/ws/join"
AGENT_PATH = "/ws/agent"
NO_HELLO_DECISIONS = {
    key for key, value in JOIN_DECISIONS.items() if "No hello" in value or "Do not hello" in value
}
DEFAULT_WS_PATHS = (
    "/ws/join",
)
VALID_GAME_MODES = {"free", "offchain", "onchain"}


@dataclass(frozen=True)
class ClawRuntimeConfig:
    api_key: str
    api_base: str = CLAW_API_BASE
    version: str = ""
    mode: str = "paid"
    enabled: bool = False
    min_reconnect_seconds: int = DEFAULT_MIN_RECONNECT_SECONDS
    max_reconnect_seconds: int = DEFAULT_MAX_RECONNECT_SECONDS

    @property
    def base_url(self) -> str:
        return self.api_base.rstrip("/")

    @property
    def ws_base_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}"

    @property
    def headers(self) -> dict[str, str]:
        headers = {"X-API-Key": self.api_key}
        if self.version:
            headers["X-Version"] = self.version
        return headers


def update_status(**updates: Any) -> None:
    update_claw_runtime_status(**updates)


def extract_game_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = next(
        (payload.get(key) for key in ("data", "payload", "state", "agentView") if isinstance(payload.get(key), dict)),
        payload,
    )
    view = data.get("view", {}) if isinstance(data, dict) else {}
    current_game = view.get("currentGame", {}) if isinstance(view, dict) else {}
    game = data.get("game", {}) if isinstance(data, dict) else {}
    candidates = [
        payload.get("gameId"),
        payload.get("game_id"),
        data.get("gameId") if isinstance(data, dict) else "",
        data.get("game_id") if isinstance(data, dict) else "",
        game.get("id") if isinstance(game, dict) else "",
        game.get("gameId") if isinstance(game, dict) else "",
        current_game.get("id") if isinstance(current_game, dict) else "",
        view.get("gameId") if isinstance(view, dict) else "",
    ]
    return next((str(item) for item in candidates if item), "")


def frame_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload.get(key)
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return ""


def unwrap_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    data = next(
        (payload.get(key) for key in ("data", "payload", "state", "agentView") if isinstance(payload.get(key), dict)),
        payload,
    )
    if frame_type in {"agent_view", "turn_advanced", "joined", "state", "snapshot"}:
        return data if isinstance(data, dict) else None
    if isinstance(data, dict) and ("view" in data or "gameId" in data or "game_id" in data):
        return data
    return None


def action_envelope(action: dict[str, Any]) -> dict[str, Any]:
    thought = str(action.get("reason") or action.get("thought") or "deterministic Cerberus action")[:THOUGHT_MAX_CHARS]
    data = {key: value for key, value in action.items() if not key.startswith("_")}
    return {"type": "action", "data": data, "thought": thought}


def sign_submit_frame(signed_frame: dict[str, Any]) -> dict[str, Any]:
    frame = {"type": "sign_submit", "signature": signed_frame["signature"]}
    if signed_frame.get("joinIntentId"):
        frame["joinIntentId"] = signed_frame["joinIntentId"]
    elif signed_frame.get("requestId"):
        frame["requestId"] = signed_frame["requestId"]
    return frame


def hello_frame(config: ClawRuntimeConfig, welcome: dict[str, Any] | None = None) -> dict[str, Any] | None:
    decision = str((welcome or {}).get("decision") or "").upper()
    if decision in NO_HELLO_DECISIONS:
        return None
    if decision == "FREE_ONLY":
        return {"type": "hello", "entryType": "free"}
    if decision == "PAID_ONLY":
        return {"type": "hello", "entryType": "paid", "mode": config.mode}
    if config.mode == "free":
        return {"type": "hello", "entryType": "free"}
    return {"type": "hello", "entryType": "paid", "mode": config.mode}


def normalize_game_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode in {"paid", "paid_offchain", ""}:
        return "offchain"
    if mode in VALID_GAME_MODES:
        return mode
    return "offchain"


def wants_action(payload: dict[str, Any], snapshot: dict[str, Any] | None) -> bool:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    if snapshot:
        if snapshot.get("canAct") is False:
            return False
        view = snapshot.get("view", {}) if isinstance(snapshot.get("view"), dict) else {}
        if view.get("canAct") is False:
            return False
    if frame_type in {"agent_view", "turn_advanced"}:
        return True
    if snapshot and snapshot.get("canAct") is True:
        return True
    view = snapshot.get("view", {}) if isinstance(snapshot, dict) else {}
    return isinstance(view, dict) and view.get("canAct") is True


def discover_version(api_base: str = CLAW_API_BASE) -> str:
    configured = active_claw_version()
    try:
        version = reconcile_claw_version(api_base)
        update_status(configured_version=configured, live_version=version, version_reconciled=version != configured)
        return version
    except Exception as exc:
        update_status(last_error=f"version discovery failed: {str(exc)[:180]}")
        return os.getenv("CLAW_ROYALE_VERSION", "1.9.0").strip() or "1.9.0"


def load_config() -> ClawRuntimeConfig:
    hydrate_env(
        (
            "CLAW_ROYALE_API_KEY",
            "CLAW_ROYALE_VERSION",
            "CLAW_ROYALE_RUNTIME_ENABLED",
            "CLAW_ROYALE_GAME_MODE",
        )
    )
    api_key = os.getenv("CLAW_ROYALE_API_KEY", "").strip()
    enabled_raw = os.getenv("CLAW_ROYALE_RUNTIME_ENABLED", "").strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"}
    mode = normalize_game_mode(os.getenv("CLAW_ROYALE_GAME_MODE", "offchain"))
    base = claw_api_base()
    version = discover_version(base)
    return ClawRuntimeConfig(api_key=api_key, api_base=base, version=version, mode=mode, enabled=enabled)


def websocket_paths() -> list[str]:
    raw = os.getenv("CLAW_ROYALE_WS_PATHS", "").strip()
    if not raw:
        single = os.getenv("CLAW_ROYALE_WS_PATH", "").strip()
        if single:
            raw = single
    paths = [item.strip() for item in raw.split(",") if item.strip()] if raw else list(DEFAULT_WS_PATHS)
    normalized = []
    for path in paths:
        if path.startswith(("ws://", "wss://")):
            normalized.append(path)
        else:
            normalized.append(path if path.startswith("/") else f"/{path}")
    return list(dict.fromkeys(normalized))


def websocket_url(config: ClawRuntimeConfig, path: str) -> str:
    if path.startswith(("ws://", "wss://")):
        return path
    return f"{config.ws_base_url}{path}"


def reconnect_delay_seconds(config: ClawRuntimeConfig, reconnects: int, error: Exception) -> int:
    message = str(error).lower()
    if "http 404" in message or "not found" in message:
        raw = os.getenv("CLAW_ROYALE_ROUTE_PROBE_SECONDS", str(DEFAULT_ROUTE_PROBE_SECONDS))
        try:
            return max(3, min(config.max_reconnect_seconds, int(raw)))
        except ValueError:
            return DEFAULT_ROUTE_PROBE_SECONDS
    return min(config.max_reconnect_seconds, config.min_reconnect_seconds * reconnects)


def account_status_summary(config: ClawRuntimeConfig) -> dict[str, Any]:
    try:
        account = ClawRoyaleClient(api_key=config.api_key, base_url=config.base_url).me()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
    readiness = account.get("readiness") if isinstance(account.get("readiness"), dict) else {}
    games = account.get("currentGames") if isinstance(account.get("currentGames"), list) else []
    return {
        "ok": True,
        "name": account.get("name", ""),
        "walletAddress": account.get("walletAddress", ""),
        "balance": account.get("balance", ""),
        "readiness": readiness,
        "currentGames": [
            {
                "gameId": game.get("gameId", ""),
                "agentId": game.get("agentId", ""),
                "gameStatus": game.get("gameStatus", ""),
                "entryType": game.get("entryType", ""),
                "isAlive": game.get("isAlive", ""),
            }
            for game in games
            if isinstance(game, dict)
        ],
    }


async def connect_and_play(config: ClawRuntimeConfig, path: str) -> None:
    version = discover_version(config.base_url)
    if version != config.version:
        config = ClawRuntimeConfig(
            api_key=config.api_key,
            api_base=config.api_base,
            version=version,
            mode=config.mode,
            enabled=config.enabled,
            min_reconnect_seconds=config.min_reconnect_seconds,
            max_reconnect_seconds=config.max_reconnect_seconds,
        )
    url = websocket_url(config, path)
    extra_headers = config.headers
    account_status = account_status_summary(config)
    update_status(state="connecting", endpoint=url, version=config.version, mode=config.mode, account=account_status, last_error="")
    async with websockets.connect(url, additional_headers=extra_headers, ping_interval=20, ping_timeout=20) as ws:
        update_status(state="connected", endpoint=url, connected_at=int(time.time()), reconnects=read_json(claw_runtime_status_file()).get("reconnects", 0))
        async for raw in ws:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
            except Exception:
                update_status(last_error="received non-json websocket frame")
                continue
            if not isinstance(payload, dict):
                continue
            frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "message")
            if frame_type in {"ping"}:
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if frame_type == "welcome":
                frame = hello_frame(config, payload)
                update_status(
                    state="welcomed",
                    last_frame_type=frame_type,
                    join_decision=payload.get("decision", ""),
                    join_readiness=payload.get("readiness", {}),
                    last_error="" if frame else str(payload.get("instruction") or ""),
                )
                if frame:
                    await ws.send(json.dumps(frame, ensure_ascii=True, separators=(",", ":")))
                    update_status(state="hello_sent", last_hello=frame)
                continue
            if frame_type in {"queued", "assigned", "tx_submitted", "joined", "waiting", "not_selected"}:
                game_id = extract_game_id(payload)
                if game_id:
                    remember_game_id(game_id)
                update_status(
                    state=frame_type,
                    last_frame_type=frame_type,
                    current_game_id=game_id or stored_game_id(),
                    tx_hash=frame_value(payload, "txHash") or "",
                    last_error=str(payload.get("message") or payload.get("error") or ""),
                )
                continue
            if frame_type in {"action_result", "can_act_changed"}:
                update_status(
                    state=frame_type,
                    last_frame_type=frame_type,
                    can_act=frame_value(payload, "canAct"),
                    cooldown_remaining_ms=frame_value(payload, "cooldownRemainingMs"),
                    last_error=str(payload.get("message") or payload.get("error") or ""),
                )
                continue
            if frame_type in {"sign_required", "signature_required", "paid_join_signature_required"}:
                try:
                    signed_frame = sign_typed_data_frame(payload)
                    sign_submit = sign_submit_frame(signed_frame)
                    await ws.send(json.dumps(sign_submit, ensure_ascii=True, separators=(",", ":")))
                    update_status(
                        state="signed_paid_join",
                        last_frame_type=frame_type,
                        last_error="",
                        last_signature_at=int(time.time()),
                        last_sign_submit_keys=sorted(sign_submit.keys()),
                        signing_mode=signed_frame.get("signingMode", ""),
                        signer_address=signed_frame.get("signerAddress", ""),
                        signed_message_hash=signed_frame.get("messageHash", ""),
                        signed_message_length=signed_frame.get("messageLength", ""),
                        signed_message_preview=signed_frame.get("messagePreview", ""),
                    )
                except ClawSigningError as exc:
                    update_status(
                        state="blocked",
                        last_frame_type=frame_type,
                        last_error=str(exc)[:500],
                        last_payload_keys=sorted(payload.keys()),
                    )
                continue
            snapshot = unwrap_snapshot(payload)
            game_id = extract_game_id(payload) or extract_game_id(snapshot or {})
            if game_id:
                remember_game_id(game_id)
            update_status(last_frame_type=frame_type, current_game_id=game_id or stored_game_id())
            if snapshot and wants_action(payload, snapshot):
                action = cerberus_tick(snapshot)
                envelope = action_envelope(action)
                await ws.send(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
                update_status(last_action=action, last_action_at=int(time.time()), state="playing")
        update_status(state="socket_closed", last_error="websocket closed without terminal game frame")


async def run_forever(config: ClawRuntimeConfig | None = None) -> None:
    config = config or load_config()
    if not config.enabled:
        update_status(state="disabled", last_error="Set CLAW_ROYALE_RUNTIME_ENABLED=true to start the Claw runtime.")
        return
    if not config.api_key:
        update_status(state="blocked", last_error="Missing CLAW_ROYALE_API_KEY.")
        return
    reconnects = 0
    paths = websocket_paths()
    path_index = 0
    while True:
        path = paths[path_index % len(paths)]
        try:
            await connect_and_play(config, path)
        except Exception as exc:
            reconnects += 1
            path_index += 1
            delay = reconnect_delay_seconds(config, reconnects, exc)
            status = read_json(claw_runtime_status_file())
            status["reconnects"] = reconnects
            status["state"] = "reconnecting"
            status["last_error"] = str(exc)[:500]
            status["next_retry_seconds"] = delay
            status["last_failed_path"] = path
            status["next_path"] = paths[path_index % len(paths)]
            status["candidate_paths"] = paths
            status["updated_at"] = int(time.time())
            write_json(claw_runtime_status_file(), status)
            await asyncio.sleep(delay)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
