"""Conservative wallet fund migration helpers for identity rotation.

The helper can sweep native Cross-chain gas tokens from old EOA wallets to the
matching new EOA wallets. Claw Royale sMoltz and smart-contract wallet balances
are reported only unless a documented transfer endpoint is added.
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import IdentityVault  # noqa: E402
from onboarding_clients import ClawRoyaleClient, OnboardingAPIError  # noqa: E402
from secret_vault import read_vault  # noqa: E402
from wallet_identity import validate_evm_address  # noqa: E402


CROSS_MAINNET_RPC = "https://mainnet.crosstoken.io:22001"
CROSS_MAINNET_CHAIN_ID = 612055
DEFAULT_GAS_LIMIT = 21_000
MIN_GAS_RESERVE_WEI = 10**15


def load_identity(path: str | Path = "") -> dict[str, Any]:
    if path:
        return read_vault(Path(path))
    return IdentityVault().load().data


def rpc_call(method: str, params: list[Any], *, rpc_url: str = CROSS_MAINNET_RPC) -> Any:
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


def wei_to_native(value: int) -> str:
    return format(Decimal(value) / Decimal(10**18), "f")


def native_balance(address: str, *, rpc_url: str = CROSS_MAINNET_RPC) -> int:
    if not validate_evm_address(address):
        return 0
    return int(rpc_call("eth_getBalance", [address, "latest"], rpc_url=rpc_url), 16)


def claw_snapshot(identity: dict[str, Any]) -> dict[str, Any]:
    api_key = identity.get("claw_royale", {}).get("api_key", "")
    if not api_key:
        return {"available": False, "reason": "missing Claw Royale API key"}
    try:
        me = ClawRoyaleClient(api_key=api_key).me()
    except OnboardingAPIError as exc:
        return {"available": False, "reason": str(exc)}
    return {
        "available": True,
        "account_id": me.get("id", ""),
        "name": me.get("name", ""),
        "balance": me.get("balance", ""),
        "walletAddress": me.get("walletAddress", ""),
        "readiness": me.get("readiness", {}),
        "currentGames": me.get("currentGames", []),
    }


def wallet_report(identity: dict[str, Any], *, rpc_url: str = CROSS_MAINNET_RPC) -> dict[str, Any]:
    wallets = identity.get("wallets", {})
    rows = {}
    for role in ("agent_eoa", "owner_eoa", "molty_royale_wallet"):
        wallet = wallets.get(role, {})
        address = wallet.get("address", "")
        rows[role] = {
            "address": address,
            "native_balance_wei": native_balance(address, rpc_url=rpc_url) if address else 0,
        }
        rows[role]["native_balance"] = wei_to_native(rows[role]["native_balance_wei"])
    return rows


def snapshot(old_identity: dict[str, Any], new_identity: dict[str, Any], *, rpc_url: str = CROSS_MAINNET_RPC) -> dict[str, Any]:
    return {
        "old_public_name": old_identity.get("public_name", ""),
        "new_public_name": new_identity.get("public_name", ""),
        "old_wallets": wallet_report(old_identity, rpc_url=rpc_url),
        "new_wallets": wallet_report(new_identity, rpc_url=rpc_url),
        "old_claw": claw_snapshot(old_identity),
        "new_claw": claw_snapshot(new_identity),
        "blocked": [
            "No documented Claw Royale endpoint is available for direct sMoltz/account-balance transfer.",
            "No documented Claw Royale smart-contract wallet withdrawal/transfer endpoint is available in this repo.",
        ],
    }


def sign_and_send_native_sweep(
    *,
    private_key: str,
    to_address: str,
    rpc_url: str = CROSS_MAINNET_RPC,
    chain_id: int = CROSS_MAINNET_CHAIN_ID,
    gas_reserve_wei: int = MIN_GAS_RESERVE_WEI,
) -> dict[str, Any]:
    from eth_account import Account  # type: ignore

    account = Account.from_key(private_key)
    balance = native_balance(account.address, rpc_url=rpc_url)
    gas_price = int(rpc_call("eth_gasPrice", [], rpc_url=rpc_url), 16)
    fee = gas_price * DEFAULT_GAS_LIMIT
    transferable = balance - fee - gas_reserve_wei
    if transferable <= 0:
        return {
            "from": account.address,
            "to": to_address,
            "sent": False,
            "reason": "balance does not exceed gas fee plus reserve",
            "balance_wei": balance,
        }
    nonce = int(rpc_call("eth_getTransactionCount", [account.address, "pending"], rpc_url=rpc_url), 16)
    tx = {
        "chainId": chain_id,
        "nonce": nonce,
        "to": to_address,
        "value": transferable,
        "gas": DEFAULT_GAS_LIMIT,
        "gasPrice": gas_price,
    }
    signed = Account.sign_transaction(tx, private_key)
    raw = signed.raw_transaction.hex()
    if not raw.startswith("0x"):
        raw = "0x" + raw
    tx_hash = rpc_call("eth_sendRawTransaction", [raw], rpc_url=rpc_url)
    return {
        "from": account.address,
        "to": to_address,
        "sent": True,
        "tx_hash": tx_hash,
        "value_wei": transferable,
        "value_native": wei_to_native(transferable),
    }


def migrate_native(
    old_identity: dict[str, Any],
    new_identity: dict[str, Any],
    *,
    execute: bool = False,
    rpc_url: str = CROSS_MAINNET_RPC,
) -> list[dict[str, Any]]:
    results = []
    for role in ("agent_eoa", "owner_eoa"):
        old_wallet = old_identity.get("wallets", {}).get(role, {})
        new_wallet = new_identity.get("wallets", {}).get(role, {})
        old_pk = old_wallet.get("private_key", "")
        new_addr = new_wallet.get("address", "")
        row = {"role": role, "from": old_wallet.get("address", ""), "to": new_addr, "executed": execute}
        if not old_pk or not validate_evm_address(new_addr):
            row["blocked"] = "missing old private key or new destination address"
        elif execute:
            row.update(sign_and_send_native_sweep(private_key=old_pk, to_address=new_addr, rpc_url=rpc_url))
        else:
            row["blocked"] = "dry run; rerun with --execute to send native sweep transactions"
        results.append(row)
    return results


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Snapshot and conservatively migrate funds after identity rotation")
    parser.add_argument("--old-vault", required=True, help="Encrypted backup vault path for the old identity")
    parser.add_argument("--new-vault", default="", help="Encrypted vault path for the new identity; defaults to active vault")
    parser.add_argument("--rpc-url", default=CROSS_MAINNET_RPC)
    parser.add_argument("--execute", action="store_true", help="Send native-token sweep transactions")
    args = parser.parse_args()

    old_identity = load_identity(args.old_vault)
    new_identity = load_identity(args.new_vault)
    report = snapshot(old_identity, new_identity, rpc_url=args.rpc_url)
    report["native_migration"] = migrate_native(
        old_identity,
        new_identity,
        execute=args.execute,
        rpc_url=args.rpc_url,
    )
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
