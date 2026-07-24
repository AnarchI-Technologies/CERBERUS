"""Secret-safe preflight for isolated live experiment agent profiles."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any

from runtime_state import normalize_agent_id


RESERVED_PORTS = frozenset({10000, 10001})
IDENTITY_KEYS = ("CLAW_ROYALE_API_KEY", "CLAW_ROYALE_ERC8004_ID", "CERBERUS_AGENT_EOA_ADDRESS")
SIGNING_KEYS = ("CERBERUS_AGENT_EOA_PRIVATE_KEY",)


def _enabled(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def read_env(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def validate_agent_profile(
    name: str,
    path: str | Path,
    *,
    production_path: str | Path | None = None,
    allow_live: bool = False,
    allow_signing: bool = False,
) -> dict[str, Any]:
    target = Path(path)
    errors: list[str] = []
    normalized = normalize_agent_id(name)
    if not normalized or normalized != name.strip().lower():
        errors.append("invalid_agent_name")
    try:
        values = read_env(target)
    except OSError:
        values = {}
        errors.append("profile_unreadable")

    if os.name != "nt" and target.exists() and stat.S_IMODE(target.stat().st_mode) & 0o077:
        errors.append("profile_permissions_must_be_0600")
    try:
        port = int(values.get("PORT", "0"))
    except ValueError:
        port = 0
    if port < 1024 or port > 65535:
        errors.append("invalid_port")
    elif port in RESERVED_PORTS:
        errors.append("reserved_port")

    live = _enabled(values.get("CLAW_ROYALE_RUNTIME_ENABLED", "false"))
    if live != allow_live:
        errors.append("runtime_must_be_disabled_for_preflight" if not allow_live else "runtime_not_enabled")
    if allow_live:
        for key in IDENTITY_KEYS[:2]:
            if not values.get(key):
                errors.append(f"missing_identity:{key}")
    if not allow_signing and any(values.get(key) for key in SIGNING_KEYS):
        errors.append("signing_key_not_allowed")

    duplicates: list[str] = []
    if production_path:
        try:
            production = read_env(production_path)
        except OSError:
            production = {}
            errors.append("production_profile_unreadable")
        for key in IDENTITY_KEYS:
            if values.get(key) and production.get(key) and hmac.compare_digest(values[key], production[key]):
                duplicates.append(key)
        if duplicates:
            errors.append("production_identity_reused")

    return {
        "ok": not errors,
        "agent_id": normalized,
        "port": port,
        "runtime_enabled": live,
        "signing_enabled": any(values.get(key) for key in SIGNING_KEYS),
        "duplicate_identity_fields": duplicates,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("name")
    parser.add_argument("profile")
    parser.add_argument("--production-profile")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--allow-signing", action="store_true")
    args = parser.parse_args()
    report = validate_agent_profile(
        args.name,
        args.profile,
        production_path=args.production_profile,
        allow_live=args.allow_live,
        allow_signing=args.allow_signing,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
