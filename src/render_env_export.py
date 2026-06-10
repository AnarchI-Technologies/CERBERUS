"""Print Render environment values from the local identity vault.

This command intentionally writes secrets only to the terminal you run it in.
Do not redirect it into a tracked file.
"""

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


def render_env(identity: dict[str, Any]) -> dict[str, str]:
    wallets = identity.get("wallets", {})
    claw = identity.get("claw_royale", {})
    agentmail = identity.get("agentmail", {})
    moltbook = identity.get("moltbook", {})
    x_account = identity.get("x_account", {})

    values = {
        "CERBERUS_PUBLIC_NAME": identity.get("public_name", ""),
        "CERBERUS_AGENT_EOA_ADDRESS": wallets.get("agent_eoa", {}).get("address", ""),
        "CERBERUS_AGENT_EOA_PRIVATE_KEY": wallets.get("agent_eoa", {}).get("private_key", ""),
        "CERBERUS_OWNER_EOA_ADDRESS": wallets.get("owner_eoa", {}).get("address", ""),
        "CERBERUS_OWNER_EOA_PRIVATE_KEY": wallets.get("owner_eoa", {}).get("private_key", ""),
        "CERBERUS_MOLTY_WALLET_ADDRESS": wallets.get("molty_royale_wallet", {}).get("address", ""),
        "CLAW_ROYALE_API_KEY": claw.get("api_key", ""),
        "CLAW_ROYALE_ACCOUNT_ID": claw.get("account_id", ""),
        "CLAW_ROYALE_ERC8004_ID": claw.get("erc8004_id", ""),
        "AGENTMAIL_API_KEY": agentmail.get("api_key", ""),
        "AGENTMAIL_INBOX_ID": agentmail.get("inbox_id", ""),
        "AGENTMAIL_EMAIL": agentmail.get("email", ""),
        "MOLTBOOK_API_KEY": moltbook.get("api_key", ""),
        "MOLTBOOK_AGENT_ID": moltbook.get("agent_id", ""),
        "MOLTBOOK_CLAIM_URL": moltbook.get("claim_url", ""),
        "X_ACCESS_TOKEN": x_account.get("access_token", ""),
        "X_REFRESH_TOKEN": x_account.get("refresh_token", ""),
    }
    return {key: str(value) for key, value in values.items() if value}


def print_powershell(values: dict[str, str]) -> None:
    for key, value in values.items():
        escaped = value.replace("'", "''")
        print(f"$env:{key} = '{escaped}'")


def print_render_list(values: dict[str, str]) -> None:
    for key, value in values.items():
        print(f"{key}={value}")


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Export local Cerberus identity values for Render")
    parser.add_argument(
        "--format",
        choices=("json", "powershell", "render"),
        default="render",
        help="Output format. render prints KEY=value lines for copying to dashboard.",
    )
    args = parser.parse_args()

    values = render_env(IdentityVault().load().data)
    if args.format == "json":
        print(json.dumps(values, ensure_ascii=True, indent=2))
    elif args.format == "powershell":
        print_powershell(values)
    else:
        print_render_list(values)
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
