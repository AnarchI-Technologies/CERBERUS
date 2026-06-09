"""Wallet creation and purpose routing for Cerberus identity."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


EVM_ADDRESS = re.compile(r"^0x[a-fA-F0-9]{40}$")


class WalletDependencyError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WalletMaterial:
    address: str
    private_key: str
    role: str
    purpose: str

    def as_secret(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "private_key": self.private_key,
            "role": self.role,
            "purpose": self.purpose,
        }


def validate_evm_address(address: str) -> bool:
    return bool(EVM_ADDRESS.fullmatch(address or ""))


def generate_evm_wallet(role: str, purpose: str) -> WalletMaterial:
    """Generate a standard EVM wallet using eth-account when available."""

    try:
        from eth_account import Account  # type: ignore
    except ImportError as exc:
        raise WalletDependencyError(
            "Install eth-account before generating EVM wallets: python -m pip install eth-account"
        ) from exc

    account = Account.create()
    return WalletMaterial(
        address=account.address,
        private_key=account.key.hex(),
        role=role,
        purpose=purpose,
    )


def wallet_for_purpose(identity: dict[str, Any], purpose: str) -> dict[str, Any]:
    role = identity.get("purpose_map", {}).get(purpose)
    if not role:
        raise KeyError(f"No wallet role mapped for purpose: {purpose}")
    wallet = identity.get("wallets", {}).get(role)
    if not wallet:
        raise KeyError(f"No wallet stored for role: {role}")
    return wallet
