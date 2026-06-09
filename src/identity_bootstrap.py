"""Hellion identity bootstrap.

Creates/stores needed wallets, registers Claw Royale and Moltbook credentials,
and provisions an AgentMail inbox when the required external API keys and
wallet libraries are available.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import DEFAULT_PUBLIC_NAME, IdentityVault  # noqa: E402
from onboarding_clients import (  # noqa: E402
    AgentMailClient,
    ClawRoyaleClient,
    MoltbookClient,
    OnboardingAPIError,
)
from secret_vault import SecretVaultError  # noqa: E402
from wallet_identity import WalletDependencyError, generate_evm_wallet, validate_evm_address  # noqa: E402


try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


HELLION_DESCRIPTION = (
    "Hellion-Meet-Your-Molty-Maker is a Cerberus-backed Claw Royale agent: "
    "tactical, survivable, and careful with secrets."
)


@dataclass(slots=True)
class BootstrapResult:
    completed: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def add_done(self, message: str) -> None:
        self.completed.append(message)

    def add_blocker(self, message: str) -> None:
        if message not in self.blockers:
            self.blockers.append(message)


def ensure_wallets(identity: dict[str, Any], result: BootstrapResult) -> None:
    wallets = identity.setdefault("wallets", {})
    if "agent_eoa" not in wallets:
        wallet = generate_evm_wallet(
            "agent_eoa",
            "Claw account wallet, EIP-712 paid joins, and reward identity.",
        )
        wallets["agent_eoa"] = wallet.as_secret()
        result.add_done(f"Generated Agent EOA {wallet.address}")

    if "owner_eoa" not in wallets:
        existing_owner = os.getenv("CERBERUS_OWNER_EOA", "")
        existing_owner_pk = os.getenv("CERBERUS_OWNER_PRIVATE_KEY", "")
        if existing_owner and existing_owner_pk and validate_evm_address(existing_owner):
            wallets["owner_eoa"] = {
                "address": existing_owner,
                "private_key": existing_owner_pk,
                "role": "owner_eoa",
                "purpose": "Owner approval and MoltyRoyale wallet ownership.",
                "source": "env",
            }
            result.add_done(f"Imported Owner EOA {existing_owner}")
        else:
            wallet = generate_evm_wallet(
                "owner_eoa",
                "Owner approval and MoltyRoyale wallet ownership.",
            )
            wallets["owner_eoa"] = wallet.as_secret()
            result.add_done(f"Generated Owner EOA {wallet.address}")


def ensure_claw_account(
    identity: dict[str, Any],
    result: BootstrapResult,
    client: ClawRoyaleClient | None = None,
) -> None:
    claw = identity.setdefault("claw_royale", {})
    claw["public_name"] = DEFAULT_PUBLIC_NAME
    if claw.get("api_key"):
        result.add_done("Claw Royale API key already stored")
        return

    agent_wallet = identity["wallets"]["agent_eoa"]
    api = client or ClawRoyaleClient()
    if not getattr(api, "onboarding_token", ""):
        owner_wallet = identity.get("wallets", {}).get("owner_eoa", {})
        owner_private_key = owner_wallet.get("private_key", "")
        if owner_private_key:
            auth = api.authenticate_wallet(owner_private_key)
            identity["claw_royale"]["owner_wallet_address"] = auth.get("ownerWalletAddress", "")
            identity["claw_royale"]["auth_wallet_role"] = "owner_eoa"
        else:
            result.add_blocker("Claw onboarding requires owner_eoa private key or CLAW_ONBOARDING_TOKEN")
            return
    response = api.create_account(DEFAULT_PUBLIC_NAME, agent_wallet["address"])
    api_key = response.get("apiKey") or response.get("api_key")
    if not api_key:
        raise OnboardingAPIError("claw_royale", 0, "POST /accounts returned no apiKey", response)
    claw.update(
        {
            "api_key": api_key,
            "account_id": response.get("accountId", ""),
            "public_id": response.get("publicId", ""),
            "agent_wallet_address": agent_wallet["address"],
        }
    )
    result.add_done(f"Registered {DEFAULT_PUBLIC_NAME} on Claw Royale and stored API key")


def ensure_molty_wallet(
    identity: dict[str, Any],
    result: BootstrapResult,
    client: ClawRoyaleClient | None = None,
) -> None:
    wallets = identity.setdefault("wallets", {})
    if "molty_royale_wallet" in wallets:
        result.add_done("MoltyRoyale wallet already stored")
        return
    claw_key = identity.get("claw_royale", {}).get("api_key", "")
    owner = wallets.get("owner_eoa", {})
    if not claw_key:
        result.add_blocker("Cannot create MoltyRoyale wallet until Claw Royale API key exists")
        return
    api = client or ClawRoyaleClient(api_key=claw_key)
    response = api.create_molty_wallet(owner.get("address", ""))
    address = response.get("walletAddress") or response.get("wallet_address")
    if not validate_evm_address(address):
        raise OnboardingAPIError("claw_royale", 0, "POST /create/wallet returned no walletAddress", response)
    wallets["molty_royale_wallet"] = {
        "address": address,
        "role": "molty_royale_wallet",
        "purpose": "Smart-contract wallet for paid-room funding and rewards.",
        "owner_role": "owner_eoa",
    }
    result.add_done(f"Stored MoltyRoyale wallet {address}")


def ensure_agentmail(
    identity: dict[str, Any],
    result: BootstrapResult,
    client: AgentMailClient | None = None,
) -> None:
    mail = identity.setdefault("agentmail", {})
    if mail.get("email"):
        result.add_done(f"AgentMail inbox already stored: {mail['email']}")
        return
    existing_inbox_id = os.getenv("AGENTMAIL_INBOX_ID", "")
    existing_email = os.getenv("AGENTMAIL_EMAIL", "")
    if existing_inbox_id or existing_email:
        inbox_id = existing_inbox_id or existing_email
        email = existing_email or (inbox_id if "@" in inbox_id else "")
        mail.update(
            {
                "provider": "agentmail",
                "api_key": os.getenv("AGENTMAIL_API_KEY", ""),
                "inbox_id": inbox_id,
                "email": email,
                "display_name": DEFAULT_PUBLIC_NAME,
                "source": "env_import",
            }
        )
        identity.setdefault("wallets", {})["agentmail_inbox"] = {
            "address": email or inbox_id,
            "role": "agentmail_inbox",
            "purpose": "Public email identity and verification inbox.",
        }
        result.add_done(f"Imported existing AgentMail inbox {email or inbox_id}")
        return
    api_key = os.getenv("AGENTMAIL_API_KEY", "")
    if not api_key and not (client and getattr(client, "api_key", "")):
        result.add_blocker(f"Set AGENTMAIL_API_KEY so {DEFAULT_PUBLIC_NAME} can create her AgentMail inbox")
        return
    api = client or AgentMailClient(api_key=api_key)
    inbox = api.create_inbox(
        username="hellion-meet-your-molty-maker",
        display_name=DEFAULT_PUBLIC_NAME,
        client_id="cerberus-hellion-primary-inbox-v1",
    )
    mail.update(
        {
            "provider": "agentmail",
            "api_key": api.api_key,
            "inbox_id": inbox.get("inbox_id") or inbox.get("inboxId") or inbox.get("email", ""),
            "email": inbox.get("email") or inbox.get("inbox_id") or "",
            "display_name": DEFAULT_PUBLIC_NAME,
        }
    )
    identity.setdefault("wallets", {})["agentmail_inbox"] = {
        "address": mail["email"],
        "role": "agentmail_inbox",
        "purpose": "Public email identity and verification inbox.",
    }
    result.add_done(f"Created AgentMail inbox {mail['email']}")


def ensure_moltbook(
    identity: dict[str, Any],
    result: BootstrapResult,
    client: MoltbookClient | None = None,
) -> None:
    moltbook = identity.setdefault("moltbook", {})
    moltbook["public_name"] = DEFAULT_PUBLIC_NAME
    if moltbook.get("api_key"):
        result.add_done("Moltbook API key already stored")
        return
    api = client or MoltbookClient()
    response = api.register_agent(DEFAULT_PUBLIC_NAME, HELLION_DESCRIPTION)
    api_key = response.get("api_key") or response.get("apiKey")
    if not api_key:
        raise OnboardingAPIError("moltbook", 0, "registration returned no api_key", response)
    moltbook.update(
        {
            "api_key": api_key,
            "agent_id": response.get("agent_id", ""),
            "claim_url": response.get("claim_url", ""),
            "verification_code": response.get("verification_code", ""),
            "email": identity.get("agentmail", {}).get("email", ""),
            "status": response.get("status", "pending_claim"),
        }
    )
    identity.setdefault("wallets", {})["moltbook_agent"] = {
        "address": moltbook.get("agent_id", DEFAULT_PUBLIC_NAME),
        "role": "moltbook_agent",
        "purpose": "Moltbook public identity and social API key.",
    }
    result.add_done(f"Registered {DEFAULT_PUBLIC_NAME} on Moltbook and stored API key")
    if moltbook.get("claim_url"):
        result.add_blocker(
            "Moltbook returned a claim URL; human/email verification may still be required"
        )


def bootstrap_identity(*, execute_external: bool = False, vault: IdentityVault | None = None) -> BootstrapResult:
    store = vault or IdentityVault().load()
    result = BootstrapResult()
    store.require_pin_ready()
    store.data["public_name"] = DEFAULT_PUBLIC_NAME

    try:
        ensure_wallets(store.data, result)
    except WalletDependencyError as exc:
        result.add_blocker(str(exc))

    if execute_external and not result.blockers:
        for step in (ensure_claw_account, ensure_molty_wallet, ensure_agentmail, ensure_moltbook):
            try:
                step(store.data, result)
            except (OnboardingAPIError, SecretVaultError) as exc:
                result.add_blocker(str(exc))
    elif not execute_external:
        result.add_blocker("External registration skipped; rerun with --execute to call Claw Royale, AgentMail, and Moltbook")

    for message in result.completed:
        store.event(message)
    for message in result.blockers:
        store.blocker(message)
    store.save()
    result.summary = store.public_summary()
    return result


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap Hellion identity into the Cerberus vault")
    parser.add_argument("--execute", action="store_true", help="Call external registration APIs")
    args = parser.parse_args()
    result = bootstrap_identity(execute_external=args.execute)
    print("completed:")
    for item in result.completed:
        print(f"- {item}")
    print("blockers:")
    for item in result.blockers:
        print(f"- {item}")
    return 0 if not result.blockers else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
