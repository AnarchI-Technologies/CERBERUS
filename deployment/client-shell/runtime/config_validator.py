"""Strict configuration validator for account and agent boundaries."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


ACCOUNT_RUNTIME_KEYS = {
    "PORT",
    "CERBERUS_BIND_HOST",
    "CERBERUS_MEMORY_DIR",
    "CERBERUS_STATE_DIR",
    "CERBERUS_LOG_DIR",
    "CERBERUS_MEMORY_BACKEND",
    "MONGODB_DATABASE",
    "CERBERUS_MONGO_COLLECTION_PREFIX",
    "CERBERUS_MANAGEMENT_HOSTNAME",
    "CERBERUS_MODEL_GATEWAY_ENABLED",
    "CLAW_ROYALE_RUNTIME_ENABLED",
    "CLAW_ROYALE_GAME_MODE",
    "CLAW_ROYALE_PAID_LAST_SLOT_ONLY",
    "CLAW_ROYALE_DISABLE_PAID_AUTO_UPGRADE",
    "CLAW_ROYALE_LOADOUT_OPTIMIZER_ENABLED",
}
ACCOUNT_CREDENTIAL_KEYS = {
    "CERBERUS_HTTP_TOKEN",
    "CERBERUS_PIN",
    "CERBERUS_OWNER_EOA_ADDRESS",
    "CERBERUS_MOLTY_WALLET_ADDRESS",
    "MONGODB_URI",
}
AGENT_RUNTIME_KEYS = {
    "CLAWROYALE_PLUGIN_ENABLED",
    "CERBERUS_RUNTIME_AGENT_ID",
    "CERBERUS_MEMORY_DIR",
    "CERBERUS_TICK_URL",
    "CLAW_ROYALE_RUNTIME_ENABLED",
}
AGENT_CREDENTIAL_KEYS = {
    "CLAW_ROYALE_API_KEY",
    "CLAW_ROYALE_ERC8004_ID",
    "CERBERUS_AGENT_EOA_PRIVATE_KEY",
    "CERBERUS_AGENT_EOA_ADDRESS",
}
TRUE_VALUES = {"1", "true", "yes", "on"}
MONGO_BACKENDS = {"mongo", "mongodb", "atlas"}
GAME_MODES = {"free", "offchain", "onchain"}
SAFE_ENV_VALUE = re.compile(r"^[A-Za-z0-9_./:@%+?&=,-]+$")
STRONG_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43,128}$")
SAFE_PIN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EVM_PRIVATE_KEY = re.compile(r"^0x[0-9a-fA-F]{64}$")
EVM_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


def read_env(path: Path, allowed: set[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.rstrip("\r\n")
            if not line or line.lstrip().startswith("#"):
                continue
            if "=" not in line:
                raise SystemExit(f"{path}:{number}: expected KEY=value")
            key, value = line.split("=", 1)
            if key not in allowed:
                raise SystemExit(f"{path}:{number}: unsupported or protected key {key}")
            if key in values:
                raise SystemExit(f"{path}:{number}: duplicate key {key}")
            normalized = value.strip()
            if normalized and not SAFE_ENV_VALUE.fullmatch(normalized):
                raise SystemExit(
                    f"{path}:{number}: values must be unquoted and use the safe environment grammar"
                )
            values[key] = normalized
    return values


def required(values: dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise SystemExit(f"{key} must be set")
    return value


def validate_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit("PORT must be numeric") from exc
    if not 1024 <= port <= 65535:
        raise SystemExit("PORT must be between 1024 and 65535")
    return port


def validate_account(args: argparse.Namespace) -> None:
    runtime = read_env(args.runtime, ACCOUNT_RUNTIME_KEYS)
    credentials = read_env(args.credentials, ACCOUNT_CREDENTIAL_KEYS)
    port = validate_port(required(runtime, "PORT"))

    if runtime.get("CERBERUS_BIND_HOST") != "127.0.0.1":
        raise SystemExit("CERBERUS_BIND_HOST must be 127.0.0.1")
    if runtime.get("CLAW_ROYALE_RUNTIME_ENABLED", "").lower() in TRUE_VALUES:
        raise SystemExit("the account core must keep CLAW_ROYALE_RUNTIME_ENABLED=false")
    if runtime.get("CLAW_ROYALE_DISABLE_PAID_AUTO_UPGRADE", "").lower() not in TRUE_VALUES:
        raise SystemExit("CLAW_ROYALE_DISABLE_PAID_AUTO_UPGRADE must remain true")
    game_mode = runtime.get("CLAW_ROYALE_GAME_MODE", "free").lower()
    if game_mode not in GAME_MODES:
        raise SystemExit("CLAW_ROYALE_GAME_MODE must be free, offchain, or onchain")
    token = required(credentials, "CERBERUS_HTTP_TOKEN")
    if not STRONG_TOKEN.fullmatch(token):
        raise SystemExit("CERBERUS_HTTP_TOKEN must be 43-128 URL-safe random characters")
    owner_address = credentials.get("CERBERUS_OWNER_EOA_ADDRESS", "")
    if owner_address and not EVM_ADDRESS.fullmatch(owner_address):
        raise SystemExit("CERBERUS_OWNER_EOA_ADDRESS must be a 0x-prefixed EVM address")

    if args.scope == "self-hosted":
        if runtime.get("CERBERUS_MEMORY_DIR") != "/var/lib/cerberus-client/memory":
            raise SystemExit("self-hosted memory must use /var/lib/cerberus-client/memory")
        return

    expected_paths = {
        "CERBERUS_MEMORY_DIR": f"{args.state_root}/{args.tenant}/memory",
        "CERBERUS_STATE_DIR": f"{args.state_root}/{args.tenant}/state",
        "CERBERUS_LOG_DIR": f"{args.log_root}/{args.tenant}",
    }
    for key, expected in expected_paths.items():
        if runtime.get(key) != expected:
            raise SystemExit(f"{key} must be the isolated tenant path {expected}")

    hostname = required(runtime, "CERBERUS_MANAGEMENT_HOSTNAME")
    if not re.fullmatch(
        r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}",
        hostname,
    ):
        raise SystemExit("CERBERUS_MANAGEMENT_HOSTNAME must be a valid lowercase hostname")

    pin = required(credentials, "CERBERUS_PIN")
    if not SAFE_PIN.fullmatch(pin):
        raise SystemExit("CERBERUS_PIN must be 8-64 URL-safe characters")

    backend = runtime.get("CERBERUS_MEMORY_BACKEND", "sqlite").lower()
    if backend != "sqlite":
        raise SystemExit(
            "managed hosting is locked to isolated sqlite until database-principal scope can be verified"
        )
    if (
        runtime.get("MONGODB_DATABASE")
        or runtime.get("CERBERUS_MONGO_COLLECTION_PREFIX")
        or credentials.get("MONGODB_URI")
    ):
        raise SystemExit("managed sqlite accounts must leave all Mongo settings blank")

    fingerprints = {
        "CERBERUS_HTTP_TOKEN": hashlib.sha256(token.encode()).digest(),
        "CERBERUS_PIN": hashlib.sha256(pin.encode()).digest(),
    }
    root = args.config_root
    if root.is_dir():
        for candidate in root.glob("*/runtime.env"):
            if candidate.parent.name == args.tenant:
                continue
            other_runtime = read_env(candidate, ACCOUNT_RUNTIME_KEYS)
            if other_runtime.get("PORT") == str(port):
                raise SystemExit("PORT is already assigned to another tenant")
            if other_runtime.get("CERBERUS_MANAGEMENT_HOSTNAME") == hostname:
                raise SystemExit("management hostname is already assigned to another tenant")
            other_credentials_path = candidate.parent / "credentials.env"
            if not other_credentials_path.is_file():
                continue
            other = read_env(other_credentials_path, ACCOUNT_CREDENTIAL_KEYS)
            for key, expected in fingerprints.items():
                value = other.get(key, "")
                if value and hashlib.sha256(value.encode()).digest() == expected:
                    raise SystemExit(f"{key} must be unique per tenant")


def validate_agent(args: argparse.Namespace) -> None:
    allowed_runtime = AGENT_RUNTIME_KEYS if args.installed else {"CLAWROYALE_PLUGIN_ENABLED"}
    runtime = read_env(args.runtime, allowed_runtime)
    credentials = read_env(args.credentials, AGENT_CREDENTIAL_KEYS)
    account_runtime = read_env(args.account_runtime, ACCOUNT_RUNTIME_KEYS)
    port = validate_port(required(account_runtime, "PORT"))
    expected_memory = f"{args.state_dir}/agents/{args.agent}/memory"

    if args.installed:
        expected = {
            "CERBERUS_RUNTIME_AGENT_ID": args.agent,
            "CERBERUS_MEMORY_DIR": expected_memory,
            "CERBERUS_TICK_URL": f"http://127.0.0.1:{port}/tick",
            "CLAW_ROYALE_RUNTIME_ENABLED": "true",
        }
        for key, value in expected.items():
            if runtime.get(key) != value:
                raise SystemExit(f"{key} must be {value}")

    private_key = credentials.get("CERBERUS_AGENT_EOA_PRIVATE_KEY", "")
    agent_address = credentials.get("CERBERUS_AGENT_EOA_ADDRESS", "")
    if private_key and not EVM_PRIVATE_KEY.fullmatch(private_key):
        raise SystemExit(
            "CERBERUS_AGENT_EOA_PRIVATE_KEY must be a 0x-prefixed 32-byte EVM key"
        )
    if agent_address and not EVM_ADDRESS.fullmatch(agent_address):
        raise SystemExit("CERBERUS_AGENT_EOA_ADDRESS must be a 0x-prefixed EVM address")

    if runtime.get("CLAWROYALE_PLUGIN_ENABLED", "").lower() in TRUE_VALUES:
        required(credentials, "CLAW_ROYALE_API_KEY")
        game_mode = account_runtime.get("CLAW_ROYALE_GAME_MODE", "free").lower()
        if game_mode in {"offchain", "onchain"}:
            required(credentials, "CERBERUS_AGENT_EOA_PRIVATE_KEY")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    subcommands = root.add_subparsers(dest="mode", required=True)

    account = subcommands.add_parser("account")
    account.add_argument("--scope", choices=("self-hosted", "managed"), required=True)
    account.add_argument("--tenant", default="")
    account.add_argument("--runtime", type=Path, required=True)
    account.add_argument("--credentials", type=Path, required=True)
    account.add_argument("--config-root", type=Path, required=True)
    account.add_argument("--state-root", required=True)
    account.add_argument("--log-root", required=True)

    agent = subcommands.add_parser("agent")
    agent.add_argument("--agent", required=True)
    agent.add_argument("--runtime", type=Path, required=True)
    agent.add_argument("--credentials", type=Path, required=True)
    agent.add_argument("--account-runtime", type=Path, required=True)
    agent.add_argument("--state-dir", required=True)
    agent.add_argument("--installed", action="store_true")
    return root


def main() -> int:
    args = parser().parse_args()
    if args.mode == "account":
        validate_account(args)
    else:
        validate_agent(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
