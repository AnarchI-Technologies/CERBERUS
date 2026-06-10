"""Deterministic Cross Forge token deployment contract.

This captures the forge-token-deployer.md rules without deploying anything.
Agent-token deploys are especially sensitive: the owner must be the Agent EOA,
so temporary-wallet deploys are forbidden for ``ai_agent``.
"""

from __future__ import annotations

from typing import Any


RPC_URL = "https://mainnet.crosstoken.io:22001"
ROUTER_ADDRESS = "0x7aF414e4d373bb332f47769c8d28A446A0C1a1E8"
TOKEN_B_ADDRESS = "0xDdF8AaA3927b8Fd5684dc2edcc7287EcB0A2122d"
TRADE_URL_BASE = "https://x.crosstoken.io/forge/token"
VENDOR_ADDRESS = "0x254465624da909e0072fbf8c32bcfc26b9fe9da9"

DEPLOY_API = {
    "client": "https://cross-console-api.crosstoken.io/api/client/mcp/builder",
    "vendor": "https://cross-console-api.crosstoken.io/api/builder/mcp",
}

VALID_AUTH_METHODS = {"client", "vendor"}
VALID_WALLET_TYPES = {"user", "tmp"}
VALID_CATEGORIES = {"game", "ai_agent"}
DEFAULT_AUTH = "vendor"
DEFAULT_WALLET = "tmp"
AGENT_TOKEN_CATEGORY = "ai_agent"
LOCAL_IMAGE_MAX_BYTES = 1024 * 1024

POOL_FUNCTION = "createPairWithVirtualReserve"
POOL_TX_OPTIONS = {
    "type": 2,
    "maxFeePerGasGwei": 100,
    "maxPriorityFeePerGasGwei": 1,
    "gasLimitMultiplierPct": 120,
    "deadlineSeconds": 300,
}

DEPLOYER_FILES = {
    "package.json": ("dotenv", "ethers"),
    "deploy-token.js": ("deploy_api", "pool_tx_builder", "cli_parser"),
}

RESULT_SHAPES = {
    "user": ("tokenAddress", "tradeLink", "unsignedTx"),
    "tmp": ("poolCreated", "tokenAddress", "tradeLink", "txHash", "blockNumber"),
}

ERRORS = {
    "symbol already in use": "Choose a different globally unique, case-insensitive symbol.",
    "HTTP 401": "Check CLIENT_KEY and CLIENT_SECRET from RampConsole.",
    "HTTP 400: invalid owner": "Use a valid 0x EVM address.",
    "pool_tx_failed": "Token may be deployed; retry pool tx manually or redeploy.",
    "image_exceeds_1mb": "Resize or compress the image.",
    "INSUFFICIENT_OUTPUT_AMOUNT": "Retry; likely transient slippage.",
}


def normalize_auth(value: str | None) -> str:
    auth = (value or DEFAULT_AUTH).strip().lower()
    if auth not in VALID_AUTH_METHODS:
        raise ValueError("Forge auth must be client or vendor.")
    return auth


def normalize_wallet(value: str | None) -> str:
    wallet = (value or DEFAULT_WALLET).strip().lower()
    if wallet not in VALID_WALLET_TYPES:
        raise ValueError("Forge wallet must be user or tmp.")
    return wallet


def normalize_category(value: str) -> str:
    category = value.strip().lower()
    if category not in VALID_CATEGORIES:
        raise ValueError("Forge category must be game or ai_agent.")
    return category


def options_for_category(category: str, *, auth: str | None = None, wallet: str | None = None) -> dict[str, str]:
    category = normalize_category(category)
    resolved_auth = normalize_auth(auth)
    resolved_wallet = normalize_wallet(wallet)
    if category == AGENT_TOKEN_CATEGORY and resolved_wallet != "user":
        raise ValueError("Agent tokens must use --wallet=user with the Agent EOA as owner.")
    return {"auth": resolved_auth, "wallet": resolved_wallet, "category": category}


def deployment_behavior(auth: str, wallet: str) -> str:
    auth = normalize_auth(auth)
    wallet = normalize_wallet(wallet)
    if wallet == "user":
        return "token_deploy_unsigned_pool_tx"
    return "token_deploy_pool_creation"


def cli_args(
    *,
    name: str,
    symbol: str,
    description: str,
    image_url: str,
    wallet_address: str,
    category: str,
    auth: str | None = None,
    wallet: str | None = None,
) -> list[str]:
    opts = options_for_category(category, auth=auth, wallet=wallet)
    return [
        "node",
        "deploy-token.js",
        f"--auth={opts['auth']}",
        f"--wallet={opts['wallet']}",
        name,
        symbol,
        description,
        image_url,
        wallet_address,
        opts["category"],
    ]


def is_unsigned_tx_result(result: dict[str, Any]) -> bool:
    return all(key in result for key in RESULT_SHAPES["user"])
