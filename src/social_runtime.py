"""Deterministic social queue for Moltbook side effects.

Game decisions can enqueue sanitized social effects without spending gameplay
actions. A separate runtime loop can drain this queue when social posting is
enabled, keeping combat and public presence isolated.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from memory_system import scrub_scalar, stable_hash
from moltybook_client import MoltyBookClient, process_social_side_effects
from runtime_state import memory_dir, read_json, write_json


SUPPORTED_EFFECTS = {"moltybook_draft", "moltybook_follow"}


def social_queue_file() -> Path:
    return memory_dir() / "social_runtime_queue.json"


def sanitize_effect(effect: dict[str, Any]) -> dict[str, Any] | None:
    effect_type = str(effect.get("type") or "")
    if effect_type not in SUPPORTED_EFFECTS:
        return None
    cleaned = {
        "id": stable_hash(effect, length=24),
        "type": effect_type,
        "created_at": int(time.time()),
        "attempts": int(effect.get("attempts") or 0),
        "status": str(effect.get("status") or "queued"),
    }
    for key in ("category", "content", "submolt", "targetAgentId", "targetHandle", "agentId", "handle", "reason"):
        if effect.get(key):
            cleaned[key] = scrub_scalar(effect.get(key), limit=500 if key == "content" else 120)
    return cleaned


def social_queue(limit: int = 200) -> list[dict[str, Any]]:
    rows = read_json(social_queue_file()).get("queue", [])
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)][-max(1, limit):]


def enqueue_social_effects(effects: list[dict[str, Any]], *, limit: int = 200) -> list[dict[str, Any]]:
    queue = social_queue(limit=limit)
    seen = {str(item.get("id") or "") for item in queue}
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        cleaned = sanitize_effect(effect)
        if not cleaned or cleaned["id"] in seen:
            continue
        queue.append(cleaned)
        seen.add(cleaned["id"])
    queue = queue[-max(1, limit):]
    write_json(social_queue_file(), {"queue": queue, "updated_at": int(time.time())})
    return queue


def drain_social_queue_once(
    *,
    client: MoltyBookClient | None = None,
    max_items: int = 5,
) -> dict[str, Any]:
    queue = social_queue()
    pending = [item for item in queue if str(item.get("status") or "queued") == "queued"][:max(0, max_items)]
    if not pending:
        return {"ok": True, "processed": 0, "results": []}
    results = process_social_side_effects(pending, client=client)
    result_by_id = {str(effect.get("id") or ""): result for effect, result in zip(pending, results)}
    for item in queue:
        result = result_by_id.get(str(item.get("id") or ""))
        if not result:
            continue
        item["attempts"] = int(item.get("attempts") or 0) + 1
        item["last_result"] = compact_result(result)
        item["status"] = "sent" if result.get("ok") else ("skipped" if result.get("skipped") else "failed")
        item["updated_at"] = int(time.time())
    write_json(social_queue_file(), {"queue": queue, "updated_at": int(time.time())})
    return {"ok": True, "processed": len(pending), "results": results}


def compact_result(result: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in result.items():
        if key in {"draft", "effect"}:
            continue
        if key in {"ok", "skipped"} and isinstance(value, bool):
            out[key] = value
        elif isinstance(value, (str, int, float)):
            out[key] = scrub_scalar(value, limit=220)
    return out
