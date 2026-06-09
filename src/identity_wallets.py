"""Inspect/export wallet material from the encrypted Cerberus identity vault."""

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


def wallet_rows(identity: dict[str, Any], *, show_private: bool = False) -> list[dict[str, Any]]:
    rows = []
    for role, wallet in sorted(identity.get("wallets", {}).items()):
        if not isinstance(wallet, dict):
            continue
        row = {
            "role": role,
            "address": wallet.get("address", ""),
            "purpose": wallet.get("purpose", ""),
        }
        if show_private and wallet.get("private_key"):
            row["private_key"] = wallet["private_key"]
        rows.append(row)
    return rows


def print_wallets(*, show_private: bool = False, role: str = "") -> None:
    identity = IdentityVault().load().data
    rows = wallet_rows(identity, show_private=show_private)
    if role:
        rows = [row for row in rows if row["role"] == role]
    if not rows:
        print("No matching wallet records found.")
        return
    for row in rows:
        print(f"role: {row['role']}")
        print(f"address: {row['address']}")
        if row.get("private_key"):
            print(f"private_key: {row['private_key']}")
        if row.get("purpose"):
            print(f"purpose: {row['purpose']}")
        print()


def export_wallets(path: str | Path, *, show_private: bool = False) -> Path:
    identity = IdentityVault().load().data
    rows = wallet_rows(identity, show_private=show_private)
    out = Path(path)
    out.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding="utf-8")
    return out


def _cli() -> int:
    parser = argparse.ArgumentParser(description="View Cerberus identity wallet records")
    parser.add_argument("--role", default="", help="Filter to a single wallet role, such as owner_eoa or agent_eoa")
    parser.add_argument("--show-private", action="store_true", help="Print private keys to this terminal")
    parser.add_argument("--export", default="", help="Write wallet records to a JSON file")
    args = parser.parse_args()

    if args.export:
        path = export_wallets(args.export, show_private=args.show_private)
        print(f"exported: {path}")
        return 0

    print_wallets(show_private=args.show_private, role=args.role)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
