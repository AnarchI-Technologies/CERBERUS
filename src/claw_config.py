"""Single source of truth for Claw Royale runtime configuration."""

from __future__ import annotations

import os
from typing import Any

import requests


CLAW_API_BASE = "https://cdn.clawroyale.ai/api"
DEFAULT_CLAW_VERSION = "1.13.1"


def claw_api_base() -> str:
    return os.getenv("CLAW_ROYALE_API_BASE", CLAW_API_BASE).strip().rstrip("/") or CLAW_API_BASE


def configured_claw_version() -> str:
    return os.getenv("CLAW_ROYALE_VERSION", "").strip()


def active_claw_version() -> str:
    return configured_claw_version() or DEFAULT_CLAW_VERSION


def fetch_live_claw_version(api_base: str | None = None) -> str:
    response = requests.get(f"{(api_base or claw_api_base()).rstrip('/')}/version", timeout=15)
    response.raise_for_status()
    payload: Any = response.json()
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("version") or "").strip()


def reconcile_claw_version(api_base: str | None = None) -> str:
    """Return the live Claw version and update this process if it drifted.

    Render env vars are immutable for the running process, so this updates
    os.environ locally. The dashboard reports the discovered version through
    runtime status; the source env can be updated later when docs confirm it.
    """

    configured = active_claw_version()
    try:
        live = fetch_live_claw_version(api_base)
    except Exception:
        return configured
    if live and live != configured:
        os.environ["CLAW_ROYALE_VERSION"] = live
        return live
    return configured


def claw_version_report(api_base: str | None = None) -> dict[str, str | bool]:
    configured = active_claw_version()
    try:
        live = fetch_live_claw_version(api_base)
    except Exception as exc:
        return {"configured": configured, "live": "", "matches": False, "error": str(exc)[:180]}
    return {"configured": configured, "live": live, "matches": bool(live and live == configured), "error": ""}
