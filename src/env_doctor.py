"""Show whether required Cerberus launch environment values are discoverable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from env_loader import env_report, hydrate_env  # noqa: E402


X_VARS = ("X_CLIENT_ID", "X_CLIENT_SECRET", "X_REDIRECT_URI")
TWITCH_VARS = ("TWITCH_USERNAME", "HELLION_TWITCH_USERNAME", "TWITCH_ACCOUNT_CREATED")
LAUNCH_VARS = (
    "CERBERUS_PIN",
    "CERBERUS_PUBLIC_NAME",
    "CERBERUS_AGENT_EOA_ADDRESS",
    "CERBERUS_AGENT_EOA_PRIVATE_KEY",
    "CERBERUS_OWNER_EOA_ADDRESS",
    "CERBERUS_OWNER_EOA_PRIVATE_KEY",
    "CERBERUS_MOLTY_WALLET_ADDRESS",
    "AGENTMAIL_API_KEY",
    "AGENTMAIL_INBOX_ID",
    "AGENTMAIL_EMAIL",
    "MOLTBOOK_API_KEY",
    "CLAW_ROYALE_API_KEY",
    "CLAW_ROYALE_FREE_FALLBACK_ENABLED",
    "CLAW_ROYALE_AVOID_EMPTY_PAID_ROOMS",
    "CERBERUS_MOLTYBOOK_ENABLED",
    *X_VARS,
    *TWITCH_VARS,
)


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Check Cerberus environment values without printing secrets")
    parser.add_argument("--x", action="store_true", help="Only check X OAuth variables")
    parser.add_argument("--hydrate", action="store_true", help="Load discoverable values into this Python process")
    args = parser.parse_args()

    names = X_VARS if args.x else LAUNCH_VARS
    if args.hydrate:
        hydrate_env(names)
    print(json.dumps(env_report(names), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
