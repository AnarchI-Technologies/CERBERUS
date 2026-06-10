"""Signing helpers for Claw paid-game frames."""

from __future__ import annotations

import os
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


def sign_typed_data_frame(payload: dict[str, Any], *, private_key: str = "") -> dict[str, Any]:
    key = private_key or agent_private_key()
    if not key:
        raise ClawSigningError("Missing CERBERUS_AGENT_EOA_PRIVATE_KEY for paid-game signature.")
    typed_data = _typed_data_from_payload(payload)
    if not typed_data:
        raise ClawSigningError("Paid-game signature frame did not include typedData/domain+types+message.")
    try:
        from eth_account import Account  # type: ignore
        from eth_account.messages import encode_typed_data  # type: ignore
    except ImportError as exc:
        raise ClawSigningError("eth-account is required for paid-game signing.") from exc
    try:
        signable = encode_typed_data(full_message=typed_data)
        signed = Account.sign_message(signable, private_key=key)
    except Exception as exc:
        raise ClawSigningError(f"Could not sign paid-game typed data: {str(exc)[:240]}") from exc
    signature = signed.signature.hex()
    if not signature.startswith("0x"):
        signature = "0x" + signature
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    request_id = data.get("requestId") or data.get("id") or data.get("nonce") or payload.get("requestId") or ""
    out = {"type": "signature", "signature": signature}
    if request_id:
        out["requestId"] = str(request_id)
    return out
