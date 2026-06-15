"""Shared runtime state files for Render dashboard and Claw worker."""

from __future__ import annotations

import json
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, utc_now


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
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
