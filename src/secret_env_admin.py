"""Secret-safe local and Render environment updates."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests

from memory_system import scrub_scalar


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV_PATH = ROOT / ".env"
RENDER_API_BASE = "https://api.render.com/v1"


def update_secret_targets(
    *,
    values: dict[str, str],
    dotenv_path: str | Path = DEFAULT_DOTENV_PATH,
    update_render: bool = False,
    render_service_id: str = "",
    render_api_key: str = "",
) -> dict[str, Any]:
    changed = upsert_dotenv_values(values, dotenv_path=dotenv_path)
    for key, value in values.items():
        os.environ[key] = value
    render_results: list[dict[str, Any]] = []
    if update_render:
        service_id = render_service_id or os.getenv("RENDER_SERVICE_ID") or os.getenv("CERBERUS_RENDER_SERVICE_ID") or ""
        api_key = render_api_key or os.getenv("RENDER_API_KEY") or ""
        if not service_id or not api_key:
            render_results.append(
                {
                    "ok": False,
                    "reason": "missing_render_credentials",
                    "service_id_present": bool(service_id),
                    "api_key_present": bool(api_key),
                }
            )
        else:
            for key, value in values.items():
                render_results.append(update_render_env_var(service_id=service_id, key=key, value=value, api_key=api_key))
    return {
        "ok": True,
        "dotenv_path": str(Path(dotenv_path)),
        "updated_keys": sorted(values),
        "changed_keys": changed,
        "render_results": render_results,
    }


def resolve_secret_value(preferred: str, *fallback_env_keys: str) -> str:
    if preferred.strip():
        return preferred.strip()
    for key in fallback_env_keys:
        value = os.getenv(key, "").strip()
        if value:
            return value
    return ""


def upsert_dotenv_values(values: dict[str, str], *, dotenv_path: str | Path = DEFAULT_DOTENV_PATH) -> list[str]:
    path = Path(dotenv_path)
    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    changed: list[str] = []
    for key, value in values.items():
        existing_lines, did_change = _upsert_dotenv_line(existing_lines, key, value)
        if did_change:
            changed.append(key)
    path.write_text("\n".join(existing_lines).rstrip() + "\n", encoding="utf-8")
    return changed


def _upsert_dotenv_line(lines: list[str], key: str, value: str) -> tuple[list[str], bool]:
    normalized = scrub_scalar(key, limit=80)
    encoded = _dotenv_assignment(normalized, value)
    updated = False
    changed = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip().lstrip("\ufeff")
        if stripped.startswith(f"{normalized}=") or stripped.startswith(f"export {normalized}="):
            out.append(encoded)
            updated = True
            changed = changed or (line != encoded)
        else:
            out.append(line)
    if not updated:
        out.append(encoded)
        changed = True
    return out, changed


def _dotenv_assignment(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def update_render_env_var(*, service_id: str, key: str, value: str, api_key: str) -> dict[str, Any]:
    response = requests.put(
        f"{RENDER_API_BASE}/services/{service_id}/env-vars/{key}",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
        json={"value": value},
        timeout=20,
    )
    ok = 200 <= response.status_code < 300
    body_preview = scrub_scalar(response.text, limit=220)
    return {
        "ok": ok,
        "service_id": scrub_scalar(service_id, limit=48),
        "key": scrub_scalar(key, limit=80),
        "status": response.status_code,
        "body_preview": body_preview,
    }
