"""Encrypted identity vault for Cerberus onboarding secrets."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, utc_now
from secret_vault import SecretVaultError, read_vault, write_vault


IDENTITY_VERSION = 1
DEFAULT_PUBLIC_NAME = "Hellion-Meet-Your-Molty-Maker"
DEFAULT_IDENTITY_VAULT = DEFAULT_MEMORY_DIR / "identity.vault.json"


def empty_identity(public_name: str = DEFAULT_PUBLIC_NAME) -> dict[str, Any]:
    now = utc_now()
    return {
        "type": "cerberus.identity",
        "version": IDENTITY_VERSION,
        "created_at": now,
        "updated_at": now,
        "public_name": public_name,
        "wallets": {},
        "claw_royale": {},
        "agentmail": {},
        "moltbook": {},
        "x_account": {},
        "purpose_map": {
            "claw_account_wallet": "agent_eoa",
            "claw_onboarding_auth": "owner_eoa",
            "eip712_paid_join": "agent_eoa",
            "agent_side_transactions": "agent_eoa",
            "reward_identity": "agent_eoa",
            "owner_approval": "owner_eoa",
            "molty_royale_wallet_owner": "owner_eoa",
            "paid_room_funding": "molty_royale_wallet",
            "agent_email": "agentmail_inbox",
            "moltbook_public_identity": "moltbook_agent",
        },
        "blockers": [],
        "events": [],
    }


class IdentityVault:
    """Load and persist the encrypted Hellion identity bundle."""

    def __init__(self, path: str | Path = DEFAULT_IDENTITY_VAULT):
        self.path = Path(path)
        self.data = empty_identity()

    def load(self) -> "IdentityVault":
        if not self.path.exists():
            self.data = empty_identity()
            return self
        self.data = read_vault(self.path)
        if not isinstance(self.data, dict):
            raise SecretVaultError("Identity vault payload is not an object.")
        self.data.setdefault("public_name", DEFAULT_PUBLIC_NAME)
        self.data.setdefault("wallets", {})
        self.data.setdefault("claw_royale", {})
        self.data.setdefault("agentmail", {})
        self.data.setdefault("moltbook", {})
        self.data.setdefault("x_account", {})
        self.data.setdefault("purpose_map", empty_identity()["purpose_map"])
        self.data.setdefault("blockers", [])
        self.data.setdefault("events", [])
        return self

    def save(self) -> Path:
        self.data["updated_at"] = utc_now()
        return write_vault(self.path, self.data, purpose="cerberus.identity")

    def require_pin_ready(self) -> None:
        if not os.getenv("CERBERUS_PIN"):
            raise SecretVaultError("Set CERBERUS_PIN before writing identity secrets.")

    def public_summary(self) -> dict[str, Any]:
        data = deepcopy(self.data)
        for wallet in data.get("wallets", {}).values():
            wallet.pop("private_key", None)
        for section in ("claw_royale", "agentmail", "moltbook", "x_account"):
            for key in ("api_key", "access_token", "refresh_token", "password", "private_key", "client_secret"):
                data.get(section, {}).pop(key, None)
        return data

    def event(self, message: str, **metadata: Any) -> None:
        self.data.setdefault("events", []).append(
            {"ts": utc_now(), "message": message, **metadata}
        )
        self.data["events"] = self.data["events"][-80:]

    def blocker(self, message: str, **metadata: Any) -> None:
        entry = {"ts": utc_now(), "message": message, **metadata}
        blockers = self.data.setdefault("blockers", [])
        if entry not in blockers:
            blockers.append(entry)
        self.data["blockers"] = blockers[-40:]
