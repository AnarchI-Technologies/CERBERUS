"""Check and attach Hellion's Claw Royale ERC-8004 identity token."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import IdentityVault  # noqa: E402
from onboarding_clients import ClawRoyaleClient  # noqa: E402


IDENTITY_KEYS = (
    "erc8004Id",
    "erc8004_id",
    "agentId",
    "agent_id",
    "tokenId",
    "token_id",
    "identityTokenId",
    "identity_token_id",
)


def extract_identity_id(payload: Any) -> str:
    if isinstance(payload, dict):
        for key in IDENTITY_KEYS:
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        for value in payload.values():
            found = extract_identity_id(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = extract_identity_id(item)
            if found:
                return found
    return ""


def load_client(identity: dict[str, Any]) -> ClawRoyaleClient:
    api_key = identity.get("claw_royale", {}).get("api_key", "")
    if not api_key:
        raise RuntimeError("No Claw Royale API key in identity vault.")
    return ClawRoyaleClient(api_key=api_key)


def sync_identity_status(*, vault: IdentityVault | None = None) -> dict[str, Any]:
    store = vault or IdentityVault().load()
    identity = store.data
    payload = load_client(identity).get_identity()
    token_id = extract_identity_id(payload)
    claw = identity.setdefault("claw_royale", {})
    claw["identity_status"] = payload
    if token_id:
        claw["erc8004_id"] = token_id
        claw["identity_ready"] = True
        store.event("Synced Claw Royale ERC-8004 identity token", erc8004_id=token_id)
    else:
        claw["identity_ready"] = False
        store.blocker("Claw Royale ERC-8004 identity token is missing")
    store.save()
    return {
        "identity_ready": bool(token_id),
        "erc8004_id": token_id,
        "status": payload,
    }


def attach_identity_token(token_id: int, *, vault: IdentityVault | None = None) -> dict[str, Any]:
    store = vault or IdentityVault().load()
    store.require_pin_ready()
    identity = store.data
    response = load_client(identity).post_identity(token_id)
    found = extract_identity_id(response) or str(token_id)
    claw = identity.setdefault("claw_royale", {})
    claw["erc8004_id"] = found
    claw["identity_ready"] = True
    claw["identity_status"] = response
    store.event("Attached Claw Royale ERC-8004 identity token", erc8004_id=found)
    store.save()
    return {
        "identity_ready": True,
        "erc8004_id": found,
        "status": response,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Check or attach Hellion's Claw Royale ERC-8004 identity token")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="GET /identity, store the result, and report readiness")
    attach = sub.add_parser("attach", help="POST /identity with a minted ERC-8004 token id")
    attach.add_argument("token_id", type=int)
    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(sync_identity_status(), ensure_ascii=True, indent=2))
        return 0
    if args.cmd == "attach":
        print(json.dumps(attach_identity_token(args.token_id), ensure_ascii=True, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
