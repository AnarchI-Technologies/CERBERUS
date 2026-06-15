"""Shared runtime state files for Render dashboard and Claw worker."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, utc_now


def memory_dir() -> Path:
    return Path(os.getenv("CERBERUS_MEMORY_DIR") or DEFAULT_MEMORY_DIR)


def current_game_file() -> Path:
    return memory_dir() / "current_game.json"


def claw_runtime_status_file() -> Path:
    return memory_dir() / "claw_runtime_status.json"


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


def update_claw_runtime_status(**updates: Any) -> None:
    status = read_json(claw_runtime_status_file())
    status.update({"updated_at": int(time.time()), **updates})
    try:
        write_json(claw_runtime_status_file(), status)
    except Exception:
        return


def stored_game_id() -> str:
    return str(read_json(current_game_file()).get("game_id", ""))


def remember_game_id(game_id: str) -> None:
    if game_id:
        try:
            write_json(current_game_file(), {"game_id": game_id, "updated_at": int(time.time())})
        except Exception:
            return


def clear_game_id() -> None:
    try:
        current_game_file().unlink(missing_ok=True)
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
