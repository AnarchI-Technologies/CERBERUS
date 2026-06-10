"""Signing helpers for Claw paid-game frames."""

from __future__ import annotations

import os
import json
from dataclasses import dataclass
from typing import Any


class ClawSigningError(RuntimeError):
    pass


def agent_private_key() -> str:
    return os.getenv("CERBERUS_AGENT_EOA_PRIVATE_KEY", "").strip()


@dataclass(frozen=True)
class SigningChallenge:
    mode: str
    typed_data: dict[str, Any]
    plain_message: str = ""


class HeadlessAgentWallet:
    """Agent EOA wallet instance backed by CERBERUS_AGENT_EOA_PRIVATE_KEY."""

    def __init__(self, private_key: str):
        if not private_key:
            raise ClawSigningError("Missing CERBERUS_AGENT_EOA_PRIVATE_KEY for paid-game signature.")
        try:
            from eth_account import Account  # type: ignore
        except ImportError as exc:
            raise ClawSigningError("eth-account is required for paid-game signing.") from exc
        self._account_api = Account
        self.private_key = private_key
        self.account = Account.from_key(private_key)

    @property
    def address(self) -> str:
        return str(self.account.address)

    def sign_challenge(self, challenge: SigningChallenge) -> dict[str, Any]:
        try:
            from eth_account.messages import encode_defunct, encode_typed_data  # type: ignore
        except ImportError as exc:
            raise ClawSigningError("eth-account is required for paid-game signing.") from exc
        try:
            if challenge.mode == "typed_data":
                signable = encode_typed_data(full_message=challenge.typed_data)
            else:
                signable = encode_defunct(text=challenge.plain_message)
            signed = self._account_api.sign_message(signable, private_key=self.private_key)
        except Exception as exc:
            raise ClawSigningError(f"Could not sign paid-game frame: {str(exc)[:240]}") from exc
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        message_hash = getattr(signed, "message_hash", None) or getattr(signed, "messageHash", None)
        if isinstance(message_hash, bytes):
            message_hash = "0x" + message_hash.hex()
        out = {
            "signature": signature,
            "signingMode": challenge.mode,
            "signerAddress": self.address,
        }
        if message_hash:
            out["messageHash"] = str(message_hash)
        if challenge.plain_message:
            out["messageLength"] = len(challenge.plain_message)
            out["messagePreview"] = _message_preview(challenge.plain_message)
        return out


def _typed_data_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    typed = data.get("typedData") or data.get("typed_data") or data.get("eip712") or data.get("signable")
    if isinstance(typed, dict):
        return typed
    message = data.get("message")
    if isinstance(message, str) and message.strip().startswith(("{", "[")):
        try:
            parsed = _parse_json_object(message)
        except ValueError:
            parsed = {}
        if isinstance(parsed, dict):
            typed = parsed.get("typedData") or parsed.get("typed_data") or parsed.get("eip712") or parsed
            if isinstance(typed, dict) and all(key in typed for key in ("domain", "types", "message")):
                return typed
            if isinstance(typed, dict) and all(key in typed for key in ("domain", "message")):
                inferred = _infer_typed_data(typed)
                if inferred:
                    return inferred
    if all(key in data for key in ("domain", "types", "message")):
        typed = {
            "domain": data["domain"],
            "types": data["types"],
            "message": data["message"],
        }
        if "primaryType" in data:
            typed["primaryType"] = data["primaryType"]
        return typed
    return {}


def _parse_json_object(value: str) -> Any:
    text = value.strip()
    try:
        return json.loads(text)
    except ValueError:
        decoder = json.JSONDecoder()
        parsed, _end = decoder.raw_decode(text)
        return parsed


def _infer_solidity_type(name: str, value: Any) -> str:
    lowered = name.lower()
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "uint256"
    if isinstance(value, str):
        if value.startswith("0x") and len(value) == 42:
            return "address"
        if value.isdecimal() and (lowered.endswith("id") or lowered in {"deadline", "nonce"}):
            return "uint256"
    return "string"


def _infer_typed_data(raw: dict[str, Any]) -> dict[str, Any]:
    domain = raw.get("domain")
    message = raw.get("message")
    if not isinstance(domain, dict) or not isinstance(message, dict):
        return {}
    domain_types = []
    for key, value in domain.items():
        domain_types.append({"name": key, "type": _infer_solidity_type(key, value)})
    primary_type = str(raw.get("primaryType") or raw.get("primary_type") or "JoinIntent")
    return {
        "types": {
            "EIP712Domain": domain_types,
            primary_type: [
                {"name": key, "type": _infer_solidity_type(key, value)}
                for key, value in message.items()
            ],
        },
        "primaryType": primary_type,
        "domain": domain,
        "message": message,
    }


def _message_from_payload(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    message = data.get("message") or data.get("signMessage") or data.get("sign_message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return json.dumps(message, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return ""


def _message_preview(message: str) -> str:
    return " ".join(message.replace("\r", "\n").split())[:240]


def signing_challenge_from_payload(payload: dict[str, Any]) -> SigningChallenge:
    typed_data = _typed_data_from_payload(payload)
    if typed_data:
        return SigningChallenge(mode="typed_data", typed_data=typed_data)
    plain_message = _message_from_payload(payload)
    if plain_message:
        return SigningChallenge(mode="plain_message", typed_data={}, plain_message=plain_message)
    raise ClawSigningError("Paid-game signature frame did not include typedData/domain+types+message or message.")


def sign_typed_data_frame(payload: dict[str, Any], *, private_key: str = "") -> dict[str, Any]:
    key = private_key or agent_private_key()
    challenge = signing_challenge_from_payload(payload)
    wallet = HeadlessAgentWallet(key)
    signed = wallet.sign_challenge(challenge)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    request_id = data.get("requestId") or data.get("joinIntentId") or data.get("id") or data.get("nonce") or payload.get("requestId") or ""
    join_intent_id = data.get("joinIntentId") or data.get("join_intent_id") or payload.get("joinIntentId") or ""
    out = {
        "type": "signature",
        **signed,
    }
    if request_id:
        out["requestId"] = str(request_id)
    if join_intent_id:
        out["joinIntentId"] = str(join_intent_id)
    return out
