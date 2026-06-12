"""Hellion identity bootstrap.

Creates/stores needed wallets, registers Claw Royale and Moltbook credentials,
and provisions an AgentMail inbox when the required external API keys and
wallet libraries are available.
"""

from __future__ import annotations

import argparse
import shutil
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from env_loader import hydrate_env  # noqa: E402
from identity_vault import DEFAULT_PUBLIC_NAME, DEFAULT_V2_PUBLIC_NAME, IdentityVault, empty_identity  # noqa: E402
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


DEFAULT_TWITCH_USERNAME = "hellionmoltymaker"
TWITCH_SIGNUP_URL = "https://www.twitch.tv/signup"
BOOTSTRAP_ENV_NAMES = (
    "CERBERUS_PIN",
    "CERBERUS_PUBLIC_NAME",
    "CERBERUS_OWNER_EOA",
    "CERBERUS_OWNER_PRIVATE_KEY",
    "CLAW_ONBOARDING_TOKEN",
    "AGENTMAIL_API_KEY",
    "AGENTMAIL_INBOX_ID",
    "AGENTMAIL_EMAIL",
    "TWITCH_USERNAME",
    "HELLION_TWITCH_USERNAME",
    "TWITCH_ACCOUNT_CREATED",
    "MOLTBOOK_API_KEY",
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
    ensure_wallet_roles(identity, result)


def identity_public_name(identity: dict[str, Any], override: str = "") -> str:
    return str(override or identity.get("public_name") or os.getenv("CERBERUS_PUBLIC_NAME") or DEFAULT_PUBLIC_NAME).strip()


def hellion_description(public_name: str) -> str:
    return (
        f"{public_name} is a Cerberus-backed Claw Royale agent: "
        "tactical, survivable, and careful with secrets."
    )


def _slug(value: str) -> str:
    return "-".join("".join(ch.lower() if ch.isalnum() else "-" for ch in value).split("-"))


def moltbook_name(public_name: str) -> str:
    configured = os.getenv("MOLTBOOK_PUBLIC_NAME", "").strip()
    if configured:
        return configured[:30]
    if len(public_name) <= 30:
        return public_name
    if public_name == DEFAULT_V2_PUBLIC_NAME:
        return "Hellion-Molty-Maker-v2"
    return _slug(public_name)[:30].strip("-_") or "Hellion"


def ensure_wallet_roles(
    identity: dict[str, Any],
    result: BootstrapResult,
    *,
    force_generate: bool = False,
    allow_env_owner: bool = True,
) -> None:
    wallets = identity.setdefault("wallets", {})
    if force_generate or "agent_eoa" not in wallets:
        wallet = generate_evm_wallet(
            "agent_eoa",
            "Claw account wallet, EIP-712 paid joins, and reward identity.",
        )
        wallets["agent_eoa"] = wallet.as_secret()
        result.add_done(f"Generated Agent EOA {wallet.address}")

    if force_generate or "owner_eoa" not in wallets:
        existing_owner = os.getenv("CERBERUS_OWNER_EOA", "")
        existing_owner_pk = os.getenv("CERBERUS_OWNER_PRIVATE_KEY", "")
        if allow_env_owner and existing_owner and existing_owner_pk and validate_evm_address(existing_owner):
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
    public_name = identity_public_name(identity)
    claw["public_name"] = public_name
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
    response = api.create_account(public_name, agent_wallet["address"])
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
    result.add_done(f"Registered {public_name} on Claw Royale and stored API key")


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
    public_name = identity_public_name(identity)
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
                "display_name": public_name,
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
        result.add_blocker(f"Set AGENTMAIL_API_KEY so {public_name} can create her AgentMail inbox")
        return
    api = client or AgentMailClient(api_key=api_key)
    inbox = api.create_inbox(
        username=_slug(public_name),
        display_name=public_name,
        client_id=f"cerberus-{_slug(public_name)}-primary-inbox-v1",
    )
    mail.update(
        {
            "provider": "agentmail",
            "api_key": api.api_key,
            "inbox_id": inbox.get("inbox_id") or inbox.get("inboxId") or inbox.get("email", ""),
            "email": inbox.get("email") or inbox.get("inbox_id") or "",
            "display_name": public_name,
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
    public_name = identity_public_name(identity)
    service_name = moltbook_name(public_name)
    moltbook["public_name"] = service_name
    moltbook["canonical_public_name"] = public_name
    if moltbook.get("api_key"):
        result.add_done("Moltbook API key already stored")
        return
    api = client or MoltbookClient()
    response = api.register_agent(service_name, hellion_description(public_name))
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
        "address": moltbook.get("agent_id", service_name),
        "role": "moltbook_agent",
        "purpose": "Moltbook public identity and social API key.",
    }
    result.add_done(f"Registered {service_name} on Moltbook and stored API key")
    if moltbook.get("claim_url"):
        result.add_blocker(
            "Moltbook returned a claim URL; external claim verification may still be required"
        )


def ensure_twitch_account(identity: dict[str, Any], result: BootstrapResult) -> None:
    twitch = identity.setdefault("twitch_account", {})
    if str(twitch.get("signup_status") or "").lower() in {"created", "linked", "verified"}:
        result.add_done(f"Twitch account already tracked: {twitch.get('username', DEFAULT_TWITCH_USERNAME)}")
        return

    agentmail = identity.get("agentmail", {})
    email = str(twitch.get("email") or agentmail.get("email") or os.getenv("AGENTMAIL_EMAIL", "")).strip()
    username = str(
        os.getenv("TWITCH_USERNAME")
        or os.getenv("HELLION_TWITCH_USERNAME")
        or twitch.get("username")
        or DEFAULT_TWITCH_USERNAME
    ).strip()
    created = os.getenv("TWITCH_ACCOUNT_CREATED", "").strip().lower() in {"1", "true", "yes", "created", "linked"}
    twitch.update(
        {
            "provider": "twitch",
            "username": username,
            "display_name": "Hellion",
            "email": email,
            "signup_url": TWITCH_SIGNUP_URL,
            "signup_status": "created" if created else "external_verification_required",
            "source": "agentmail_email",
        }
    )
    identity.setdefault("wallets", {})["twitch_account"] = {
        "address": username,
        "role": "twitch_account",
        "purpose": "Public Twitch channel identity for Hellion streams.",
    }
    if not email:
        result.add_blocker("Create or import Hellion's AgentMail email before Twitch signup")
    elif created:
        result.add_done(f"Tracked Twitch account {username} using {email}")
    else:
        result.add_blocker(f"Complete Hellion's Twitch signup and verification at {TWITCH_SIGNUP_URL} using {email}")


def backup_vault_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_name(f"{path.stem}.{stamp}.backup{path.suffix}")
    shutil.copy2(path, backup)
    return backup


def rotate_identity_v2(
    *,
    execute_external: bool = False,
    vault: IdentityVault | None = None,
    public_name: str = DEFAULT_V2_PUBLIC_NAME,
) -> BootstrapResult:
    hydrate_env(BOOTSTRAP_ENV_NAMES)
    store = vault or IdentityVault().load()
    result = BootstrapResult()
    store.require_pin_ready()
    old_summary = store.public_summary()
    backup = backup_vault_file(store.path)

    preserved_agentmail = deepcopy(store.data.get("agentmail", {}))
    preserved_agentmail_wallet = deepcopy(store.data.get("wallets", {}).get("agentmail_inbox", {}))
    preserved_twitch = deepcopy(store.data.get("twitch_account", {}))
    preserved_twitch_wallet = deepcopy(store.data.get("wallets", {}).get("twitch_account", {}))

    store.data = empty_identity(public_name)
    if preserved_agentmail:
        store.data["agentmail"] = preserved_agentmail
    if preserved_agentmail_wallet:
        store.data.setdefault("wallets", {})["agentmail_inbox"] = preserved_agentmail_wallet
    if preserved_twitch:
        store.data["twitch_account"] = preserved_twitch
    if preserved_twitch_wallet:
        store.data.setdefault("wallets", {})["twitch_account"] = preserved_twitch_wallet
    store.data["previous_identity"] = {
        "rotated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "public_name": old_summary.get("public_name", ""),
        "backup_path": str(backup or ""),
        "agent_eoa_address": old_summary.get("wallets", {}).get("agent_eoa", {}).get("address", ""),
        "owner_eoa_address": old_summary.get("wallets", {}).get("owner_eoa", {}).get("address", ""),
        "molty_royale_wallet_address": old_summary.get("wallets", {}).get("molty_royale_wallet", {}).get("address", ""),
        "claw_account_id": old_summary.get("claw_royale", {}).get("account_id", ""),
    }
    if backup:
        result.add_done(f"Backed up previous encrypted identity vault to {backup}")

    try:
        ensure_wallet_roles(store.data, result, force_generate=True, allow_env_owner=False)
    except WalletDependencyError as exc:
        result.add_blocker(str(exc))

    if execute_external and not result.blockers:
        for step in (ensure_claw_account, ensure_molty_wallet, ensure_agentmail, ensure_twitch_account, ensure_moltbook):
            try:
                step(store.data, result)
            except (OnboardingAPIError, SecretVaultError) as exc:
                result.add_blocker(str(exc))
    elif not execute_external:
        result.add_blocker("External registration skipped; rerun with --execute to register the v2 identity")

    for message in result.completed:
        store.event(message)
    for message in result.blockers:
        store.blocker(message)
    store.save()
    result.summary = store.public_summary()
    return result


def bootstrap_identity(
    *,
    execute_external: bool = False,
    vault: IdentityVault | None = None,
    public_name: str = "",
) -> BootstrapResult:
    hydrate_env(BOOTSTRAP_ENV_NAMES)
    store = vault or IdentityVault().load()
    result = BootstrapResult()
    store.require_pin_ready()
    store.data["public_name"] = identity_public_name(store.data, public_name)

    try:
        ensure_wallets(store.data, result)
    except WalletDependencyError as exc:
        result.add_blocker(str(exc))

    if execute_external and not result.blockers:
        for step in (ensure_claw_account, ensure_molty_wallet, ensure_agentmail, ensure_twitch_account, ensure_moltbook):
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
    parser.add_argument("--public-name", default="", help="Public name for newly registered services")
    parser.add_argument(
        "--rotate-v2",
        action="store_true",
        help="Backup the current encrypted vault, generate fresh v2 wallets, and register the v2 identity",
    )
    args = parser.parse_args()
    if args.rotate_v2:
        result = rotate_identity_v2(execute_external=args.execute, public_name=args.public_name or DEFAULT_V2_PUBLIC_NAME)
    else:
        result = bootstrap_identity(execute_external=args.execute, public_name=args.public_name)
    print("completed:")
    for item in result.completed:
        print(f"- {item}")
    print("blockers:")
    for item in result.blockers:
        print(f"- {item}")
    return 0 if not result.blockers else 1


if __name__ == "__main__":
    raise SystemExit(_cli())
