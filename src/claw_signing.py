"""Signing helpers for Claw paid-game frames."""

from __future__ import annotations

import os
import json
from typing import Any


class ClawSigningError(RuntimeError):
    pass


def agent_private_key() -> str:
    return os.getenv("CERBERUS_AGENT_EOA_PRIVATE_KEY", "").strip()


def _typed_data_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    typed = data.get("typedData") or data.get("typed_data") or data.get("eip712") or data.get("signable")
    if isinstance(typed, dict):
        return typed
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


def _message_from_payload(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    message = data.get("message") or data.get("signMessage") or data.get("sign_message")
    if isinstance(message, str):
        return message
    if isinstance(message, dict):
        return json.dumps(message, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return ""


def sign_typed_data_frame(payload: dict[str, Any], *, private_key: str = "") -> dict[str, Any]:
    key = private_key or agent_private_key()
    if not key:
        raise ClawSigningError("Missing CERBERUS_AGENT_EOA_PRIVATE_KEY for paid-game signature.")
    typed_data = _typed_data_from_payload(payload)
    plain_message = "" if typed_data else _message_from_payload(payload)
    if not typed_data and not plain_message:
        raise ClawSigningError("Paid-game signature frame did not include typedData/domain+types+message or message.")
    try:
        from eth_account import Account  # type: ignore
        from eth_account.messages import encode_defunct, encode_typed_data  # type: ignore
    except ImportError as exc:
        raise ClawSigningError("eth-account is required for paid-game signing.") from exc
    try:
        signable = encode_typed_data(full_message=typed_data) if typed_data else encode_defunct(text=plain_message)
        account = Account.from_key(key)
        signed = Account.sign_message(signable, private_key=key)
    except Exception as exc:
        raise ClawSigningError(f"Could not sign paid-game frame: {str(exc)[:240]}") from exc
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    request_id = data.get("requestId") or data.get("joinIntentId") or data.get("id") or data.get("nonce") or payload.get("requestId") or ""
    join_intent_id = data.get("joinIntentId") or data.get("join_intent_id") or payload.get("joinIntentId") or ""
    message_hash = getattr(signed, "message_hash", None) or getattr(signed, "messageHash", None)
    if isinstance(message_hash, bytes):
        message_hash = "0x" + message_hash.hex()
    out = {
        "type": "signature",
        "signature": signature,
        "signingMode": "typed_data" if typed_data else "plain_message",
        "signerAddress": account.address,
    }
    if message_hash:
        out["messageHash"] = str(message_hash)
    if plain_message:
        out["messageLength"] = len(plain_message)
    if request_id:
        out["requestId"] = str(request_id)
    if join_intent_id:
        out["joinIntentId"] = str(join_intent_id)
    return out
