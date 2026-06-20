"""Shared runtime state files for Render dashboard and Claw worker."""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, atomic_write_text, scrub_scalar, stable_hash, utc_now


_RUNTIME_AGENT_ID: ContextVar[str] = ContextVar("cerberus_runtime_agent_id", default="")


def memory_dir() -> Path:
    return Path(os.getenv("CERBERUS_MEMORY_DIR") or DEFAULT_MEMORY_DIR)


def normalize_agent_id(agent_id: str | None = None) -> str:
    value = str(agent_id if agent_id is not None else _RUNTIME_AGENT_ID.get() or "").strip().lower()
    return "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})[:32]


def set_runtime_agent_id(agent_id: str):
    return _RUNTIME_AGENT_ID.set(normalize_agent_id(agent_id))


def reset_runtime_agent_id(token: Any) -> None:
    _RUNTIME_AGENT_ID.reset(token)


def runtime_agent_id() -> str:
    return normalize_agent_id()


def _agent_suffix(agent_id: str | None = None) -> str:
    normalized = normalize_agent_id(agent_id)
    return f"_{normalized}" if normalized else ""


def current_game_file(agent_id: str | None = None) -> Path:
    return memory_dir() / f"current_game{_agent_suffix(agent_id)}.json"


def claw_runtime_status_file(agent_id: str | None = None) -> Path:
    return memory_dir() / f"claw_runtime_status{_agent_suffix(agent_id)}.json"


def stream_chat_file() -> Path:
    return memory_dir() / "stream_chat.json"


def hellion_voice_lab_file() -> Path:
    return memory_dir() / "hellion_voice_lab.json"


def owner_messages_file() -> Path:
    return memory_dir() / "owner_messages.json"


def match_evidence_file() -> Path:
    return memory_dir() / "match_evidence.json"


def suggested_edits_file() -> Path:
    return memory_dir() / "suggested_edits.json"


def stale_paid_rooms_file() -> Path:
    return memory_dir() / "stale_paid_rooms.json"


def read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
    except Exception:
        return {}
    return {}


def write_json(path: Path, payload: dict[str, Any]) -> bool:
    try:
        atomic_write_text(
            path,
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def update_claw_runtime_status(agent_id: str | None = None, **updates: Any) -> None:
    active_agent = normalize_agent_id(agent_id)
    status = read_json(claw_runtime_status_file(active_agent))
    if active_agent:
        updates.setdefault("agent_id", active_agent)
    status.update({"updated_at": int(time.time()), **updates})
    try:
        write_json(claw_runtime_status_file(active_agent), status)
    except Exception:
        return


def stored_game_id(agent_id: str | None = None) -> str:
    return str(read_json(current_game_file(agent_id)).get("game_id", ""))


def remember_game_id(game_id: str, agent_id: str | None = None) -> None:
    if game_id:
        try:
            payload = {"game_id": game_id, "updated_at": int(time.time())}
            active_agent = normalize_agent_id(agent_id)
            if active_agent:
                payload["agent_id"] = active_agent
            write_json(current_game_file(active_agent), payload)
        except Exception:
            return


def clear_game_id(agent_id: str | None = None) -> None:
    try:
        current_game_file(agent_id).unlink(missing_ok=True)
    except Exception:
        return


def stream_chat_messages(limit: int = 50) -> list[dict[str, Any]]:
    messages = read_json(stream_chat_file()).get("messages", [])
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)][-limit:]


def append_stream_chat(message: dict[str, Any], *, limit: int = 50) -> list[dict[str, Any]]:
    messages = stream_chat_messages(limit=limit)
    messages.append(message)
    messages = messages[-limit:]
    try:
        write_json(stream_chat_file(), {"messages": messages, "updated_at": int(time.time())})
    except Exception:
        return messages
    return messages


def owner_messages(limit: int = 25) -> list[dict[str, Any]]:
    messages = read_json(owner_messages_file()).get("messages", [])
    if not isinstance(messages, list):
        return []
    return [item for item in messages if isinstance(item, dict)][-limit:]


def append_owner_message(message: dict[str, Any], *, limit: int = 25) -> list[dict[str, Any]]:
    messages = owner_messages(limit=limit)
    messages.append(message)
    messages = messages[-limit:]
    try:
        write_json(owner_messages_file(), {"messages": messages, "updated_at": int(time.time())})
    except Exception:
        return messages
    return messages


def _scrub_mapping(payload: dict[str, Any], *, text_limit: int = 220) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in payload.items():
        safe_key = scrub_scalar(key, limit=48)
        if not safe_key:
            continue
        if isinstance(value, dict):
            cleaned[safe_key] = _scrub_mapping(value, text_limit=text_limit)
        elif isinstance(value, list):
            cleaned[safe_key] = [
                _scrub_mapping(item, text_limit=text_limit) if isinstance(item, dict) else scrub_scalar(item, limit=text_limit)
                for item in value[:12]
            ]
        elif isinstance(value, bool) or value is None:
            cleaned[safe_key] = value
        elif isinstance(value, (int, float)):
            cleaned[safe_key] = value
        else:
            cleaned[safe_key] = scrub_scalar(value, limit=text_limit)
    return cleaned


def match_evidence(limit: int = 200) -> list[dict[str, Any]]:
    entries = read_json(match_evidence_file()).get("entries", [])
    if not isinstance(entries, list):
        return []
    return [item for item in entries if isinstance(item, dict)][-max(1, limit):]


def append_match_evidence(entry: dict[str, Any], *, limit: int = 1500) -> list[dict[str, Any]]:
    entries = match_evidence(limit=limit)
    cleaned = _scrub_mapping(entry, text_limit=260)
    cleaned.setdefault("created_at", utc_now())
    entries.append(cleaned)
    entries = entries[-max(1, limit):]
    try:
        write_json(match_evidence_file(), {"entries": entries, "updated_at": int(time.time())})
    except Exception:
        return entries
    return entries


def suggested_edits(limit: int = 100) -> list[dict[str, Any]]:
    edits = read_json(suggested_edits_file()).get("edits", [])
    if not isinstance(edits, list):
        return []
    return [item for item in edits if isinstance(item, dict)][-max(1, limit):]


def append_suggested_edit(edit: dict[str, Any], *, limit: int = 100) -> list[dict[str, Any]]:
    edits = suggested_edits(limit=limit)
    cleaned = _scrub_mapping(edit, text_limit=320)
    signature = stable_hash(
        {
            "title": cleaned.get("title", ""),
            "file": cleaned.get("file", ""),
            "detector": cleaned.get("detector", ""),
            "symptom": cleaned.get("symptom", ""),
        },
        length=18,
    )
    existing = next((item for item in edits if item.get("id") == signature), None)
    if existing is not None:
        existing["last_seen_at"] = utc_now()
        existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
        existing["latest_evidence"] = cleaned.get("evidence", {})
    else:
        cleaned.setdefault("status", "open")
        cleaned.setdefault("created_at", utc_now())
        cleaned.setdefault("last_seen_at", cleaned["created_at"])
        cleaned.setdefault("seen_count", 1)
        cleaned["id"] = signature
        edits.append(cleaned)
    edits = edits[-max(1, limit):]
    try:
        write_json(suggested_edits_file(), {"edits": edits, "updated_at": int(time.time())})
    except Exception:
        return edits
    return edits


def update_suggested_edit_status(edit_id: str, status: str, *, note: str = "", limit: int = 100) -> dict[str, Any]:
    allowed = {"open", "approved", "rejected", "archived"}
    normalized = status.strip().lower()
    if normalized not in allowed:
        return {"ok": False, "error": "invalid_status", "allowed": sorted(allowed)}
    edits = suggested_edits(limit=limit)
    edit = next((item for item in edits if str(item.get("id") or "") == str(edit_id)), None)
    if edit is None:
        return {"ok": False, "error": "not_found"}
    edit["status"] = normalized
    edit["reviewed_at"] = utc_now()
    if note:
        edit["review_note"] = scrub_scalar(note, limit=220)
    write_json(suggested_edits_file(), {"edits": edits, "updated_at": int(time.time())})
    return {"ok": True, "edit": edit, "suggested_edits": edits}


def stale_paid_rooms(limit: int = 100) -> list[dict[str, Any]]:
    rows = read_json(stale_paid_rooms_file()).get("rooms", [])
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)][-max(1, limit):]


def remember_stale_paid_room(room_id: str, *, reason: str = "", limit: int = 100) -> list[dict[str, Any]]:
    room_id = scrub_scalar(room_id, limit=80)
    if not room_id:
        return stale_paid_rooms(limit=limit)
    rows = stale_paid_rooms(limit=limit)
    existing = next((item for item in rows if item.get("room_id") == room_id), None)
    if existing:
        existing["last_seen_at"] = utc_now()
        existing["seen_count"] = int(existing.get("seen_count") or 1) + 1
        if reason:
            existing["reason"] = scrub_scalar(reason, limit=180)
    else:
        rows.append(
            {
                "room_id": room_id,
                "reason": scrub_scalar(reason, limit=180),
                "created_at": utc_now(),
                "last_seen_at": utc_now(),
                "seen_count": 1,
            }
        )
    rows = rows[-max(1, limit):]
    write_json(stale_paid_rooms_file(), {"rooms": rows, "updated_at": int(time.time())})
    return rows


def append_hellion_owner_response(
    text: str,
    *,
    command_id: str = "",
    status: str = "heard",
    limit: int = 25,
) -> list[dict[str, Any]]:
    return append_owner_message(
        {
            "kind": "hellion_response",
            "author": "Hellion",
            "text": text,
            "status": status,
            "command_id": command_id,
            "created_at": utc_now(),
        },
        limit=limit,
    )


def last_hellion_response_for_command(command_id: str) -> dict[str, Any]:
    if not command_id:
        return {}
    for message in reversed(owner_messages()):
        if (
            isinstance(message, dict)
            and message.get("kind") == "hellion_response"
            and message.get("command_id") == command_id
        ):
            return message
    return {}


def hellion_voice_lab() -> dict[str, Any]:
    return read_json(hellion_voice_lab_file())
