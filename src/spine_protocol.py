"""Read-first client contract shared by Helm and Vigil.

Spine is the account-scoped orchestration boundary.  This module deliberately
selects a bounded public view instead of forwarding the internal dashboard
payload, which contains host paths and administrative configuration details.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Final, Iterable


SCHEMA_VERSION: Final = "cerberus.spine.v1"
SURFACES: Final = frozenset({"helm", "vigil", "unknown"})
REACH_LEVELS: Final = frozenset({"read-only", "standard", "remote", "elevated", "time-limited"})
READ_CAPABILITIES: Final = ("health.read", "session.read", "snapshot.read")
STANDARD_CAPABILITIES: Final = (*READ_CAPABILITIES, "extensions.read")


class SpineProtocolError(ValueError):
    """Raised when a client contract value violates the Spine boundary."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_surface(value: Any) -> str:
    surface = str(value or "unknown").strip().lower()
    return surface if surface in SURFACES else "unknown"


def normalize_reach(values: Iterable[Any]) -> tuple[str, ...]:
    normalized = {str(value or "").strip().lower() for value in values}
    normalized.discard("")
    invalid = normalized - REACH_LEVELS
    if invalid:
        raise SpineProtocolError(f"unknown Reach level: {sorted(invalid)[0]}")
    normalized.add("read-only")
    return tuple(sorted(normalized))


def capabilities_for(reach: Iterable[Any]) -> tuple[str, ...]:
    levels = normalize_reach(reach)
    return STANDARD_CAPABILITIES if "standard" in levels else READ_CAPABILITIES


def session_document(*, surface: Any, reach: Iterable[Any], observed_at: str | None = None) -> dict[str, Any]:
    levels = normalize_reach(reach)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "session",
        "observed_at": observed_at or utc_now(),
        "system": "CERBERUS",
        "surface": normalize_surface(surface),
        "reach": list(levels),
        "capabilities": list(capabilities_for(levels)),
    }


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _bounded_text(value: Any, limit: int = 240) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def _bounded_strings(value: Any, *, limit: int = 12, item_limit: int = 240) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_bounded_text(item, item_limit) for item in value[:limit] if str(item or "").strip()]


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def snapshot_document(
    source: dict[str, Any],
    *,
    surface: Any,
    reach: Iterable[Any],
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Build the stable client snapshot without leaking internal host state."""
    runtime = _mapping(source.get("claw_runtime"))
    memory = _mapping(source.get("longterm_memory"))
    launch = _mapping(source.get("launch"))
    autonomy = _mapping(source.get("autonomy"))
    levels = normalize_reach(reach)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "snapshot",
        "observed_at": observed_at or utc_now(),
        "system": "CERBERUS",
        "surface": normalize_surface(surface),
        "reach": list(levels),
        "health": {
            "ok": bool(source.get("ok")),
            "memory_writable": bool(source.get("memory_writable")),
        },
        "runtime": {
            "state": _bounded_text(runtime.get("state"), 80),
            "mode": _bounded_text(runtime.get("mode"), 80),
            "game_status": _bounded_text(runtime.get("game_status"), 80),
            "current_game_id": _bounded_text(source.get("current_game_id"), 120),
            "gameplay_ready": bool(runtime.get("gameplay_ready")),
            "can_act": bool(runtime.get("can_act")),
            "last_frame_type": _bounded_text(runtime.get("last_frame_type"), 80),
            "last_error": _bounded_text(runtime.get("last_error"), 240),
            "updated_at": runtime.get("updated_at", 0),
        },
        "memory": {
            "items": _integer(memory.get("items")),
            "bytes": _integer(memory.get("bytes")),
        },
        "activity": {
            "public_thought": _bounded_text(source.get("public_thought"), 240),
            "autonomy": {
                "suggested_edits": _integer(autonomy.get("suggested_edits")),
                "match_evidence": _integer(autonomy.get("match_evidence")),
                "stale_paid_rooms": _integer(autonomy.get("stale_paid_rooms")),
                "social_queue": _integer(autonomy.get("social_queue")),
            },
        },
        "launch": {
            "ok": bool(launch.get("ok")),
            "blockers": _bounded_strings(launch.get("blockers")),
        },
    }


def error_document(code: str, *, message: str = "", observed_at: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "error",
        "observed_at": observed_at or utc_now(),
        "system": "CERBERUS",
        "error": {"code": _bounded_text(code, 80), "message": _bounded_text(message, 240)},
    }
