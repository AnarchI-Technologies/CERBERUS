"""Check and attach Hellion's Claw Royale ERC-8004 identity token."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import IdentityVault  # noqa: E402
from onboarding_clients import ClawRoyaleClient  # noqa: E402
from wallet_identity import validate_evm_address  # noqa: E402


CHAIN_ID = 612055
RPC_URL = "https://mainnet.crosstoken.io:22001"
IDENTITY_REGISTRY_ADDRESS = "0x8004A169FB4a3325136EB29fA0ceB6D2e539a432"
REGISTER_SELECTOR = "0x1aa3a008"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC_ADDRESS = "0" * 64
REGISTER_GAS_LIMIT = 200_000
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


def rpc_call(method: str, params: list[Any], *, rpc_url: str = RPC_URL) -> Any:
    response = requests.post(
        rpc_url,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"RPC {method} failed: {payload['error']}")
    return payload.get("result")


def wait_for_receipt(tx_hash: str, *, rpc_url: str = RPC_URL, timeout_seconds: int = 90) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        receipt = rpc_call("eth_getTransactionReceipt", [tx_hash], rpc_url=rpc_url)
        if isinstance(receipt, dict):
            return receipt
        time.sleep(3)
    raise TimeoutError(f"Timed out waiting for identity registration transaction receipt: {tx_hash}")


def extract_minted_token_id(receipt: dict[str, Any], owner_address: str) -> str:
    owner_topic = "0" * 24 + owner_address.lower().removeprefix("0x")
    registry = IDENTITY_REGISTRY_ADDRESS.lower()
    for log in receipt.get("logs", []):
        if not isinstance(log, dict):
            continue
        if str(log.get("address", "")).lower() != registry:
            continue
        topics = log.get("topics", [])
        if not isinstance(topics, list) or len(topics) < 4:
            continue
        if str(topics[0]).lower() != TRANSFER_TOPIC:
            continue
        if str(topics[1]).lower().removeprefix("0x") != ZERO_TOPIC_ADDRESS:
            continue
        if str(topics[2]).lower().removeprefix("0x") != owner_topic:
            continue
        return str(int(str(topics[3]), 16))
    return ""


def mint_identity_token(*, vault: IdentityVault | None = None, rpc_url: str = RPC_URL) -> dict[str, Any]:
    try:
        from eth_account import Account  # type: ignore
    except ImportError as exc:
        raise RuntimeError("eth-account is required to mint the ERC-8004 identity token") from exc

    store = vault or IdentityVault().load()
    store.require_pin_ready()
    identity = store.data
    owner = identity.get("wallets", {}).get("owner_eoa", {})
    private_key = str(owner.get("private_key") or "")
    owner_address = str(owner.get("address") or "")
    if not private_key:
        raise RuntimeError("No owner_eoa private key in identity vault.")
    account = Account.from_key(private_key)
    owner_address = owner_address or account.address
    if not validate_evm_address(owner_address):
        raise RuntimeError("owner_eoa address is missing or invalid.")

    nonce = int(rpc_call("eth_getTransactionCount", [account.address, "pending"], rpc_url=rpc_url), 16)
    gas_price = int(rpc_call("eth_gasPrice", [], rpc_url=rpc_url), 16)
    tx = {
        "chainId": CHAIN_ID,
        "nonce": nonce,
        "to": IDENTITY_REGISTRY_ADDRESS,
        "value": 0,
        "data": REGISTER_SELECTOR,
        "gas": REGISTER_GAS_LIMIT,
        "gasPrice": gas_price,
    }
    signed = Account.sign_transaction(tx, private_key)
    raw = signed.raw_transaction.hex()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    tx_hash = rpc_call("eth_sendRawTransaction", [raw], rpc_url=rpc_url)
    receipt = wait_for_receipt(tx_hash, rpc_url=rpc_url)
    status = str(receipt.get("status", "")).lower()
    if status not in {"0x1", "1"}:
        raise RuntimeError(f"Identity registry transaction failed: {tx_hash}")
    token_id = extract_minted_token_id(receipt, owner_address)
    if not token_id:
        raise RuntimeError(f"Identity registry transaction succeeded but token id was not found in receipt: {tx_hash}")
    response = load_client(identity).post_identity(int(token_id))
    found = extract_identity_id(response) or token_id
    claw = identity.setdefault("claw_royale", {})
    claw["erc8004_id"] = found
    claw["identity_ready"] = True
    claw["identity_mint_tx"] = tx_hash
    claw["identity_status"] = response
    store.event("Minted and attached Claw Royale ERC-8004 identity token", erc8004_id=found, tx_hash=tx_hash)
    store.save()
    return {
        "identity_ready": True,
        "erc8004_id": found,
        "tx_hash": tx_hash,
        "status": response,
    }


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
    mint = sub.add_parser("mint", help="Call IdentityRegistry.register() from owner_eoa, then POST /identity")
    mint.add_argument("--rpc-url", default=RPC_URL)
    attach = sub.add_parser("attach", help="POST /identity with a minted ERC-8004 token id")
    attach.add_argument("token_id", type=int)
    args = parser.parse_args()

    if args.cmd == "status":
        print(json.dumps(sync_identity_status(), ensure_ascii=True, indent=2))
        return 0
    if args.cmd == "mint":
        print(json.dumps(mint_identity_token(rpc_url=args.rpc_url), ensure_ascii=True, indent=2))
        return 0
    if args.cmd == "attach":
        print(json.dumps(attach_identity_token(args.token_id), ensure_ascii=True, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
