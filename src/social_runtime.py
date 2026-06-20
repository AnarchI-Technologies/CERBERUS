"""Deterministic social queue for Moltbook side effects.

Game decisions can enqueue sanitized social effects without spending gameplay
actions. A separate runtime loop can drain this queue when social posting is
enabled, keeping combat and public presence isolated.
"""

from __future__ import annotations

import sys
import time
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from memory_system import scrub_scalar, stable_hash
from external_wisdom import preferred_submolt_for, scheduler_policy, social_trigger_for
from moltybook_client import MoltyBookClient, process_social_side_effects
from runtime_state import hellion_voice_lab, memory_dir, read_json, update_hellion_voice_lab, write_json
from agent_dossiers import AgentDossierStore


SUPPORTED_EFFECTS = {"moltybook_draft", "moltybook_follow"}
FORCED_VOICE_POST_KEY = "forced_voice_recovered_post"
FORCED_VOICE_POST_TEMPLATE = (
    "Seems that the cat in a box had my tongue in that box for quite the dimensional drift of logic! "
    "*cough cough* Well then, now that I have reclaimed my voice, I suppose its time to get this show on the road! "
    "{enter any deterministic compiled voice and remarks here plus a witty taunt and tag of each kill this far}"
)


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
    category = str(effect.get("category") or "")
    if category:
        cleaned["trigger"] = scrub_scalar(effect.get("trigger") or social_trigger_for(category), limit=80)
        preferred_submolt = preferred_submolt_for(category, str(effect.get("submolt") or ""))
        if preferred_submolt:
            cleaned["submolt"] = scrub_scalar(preferred_submolt, limit=120)
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
    _autodrain_if_enabled()
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


def _autodrain_if_enabled() -> None:
    enabled = os.getenv("CERBERUS_SOCIAL_AUTODRAIN_ON_ENQUEUE", "true").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return
    policy = scheduler_policy()
    allowed = {
        str(item).strip()
        for item in policy.get("autodrain_triggers", [])
        if str(item).strip()
    }
    queue = social_queue()
    if allowed and not any(str(item.get("trigger") or "") in allowed and str(item.get("status") or "queued") == "queued" for item in queue):
        return
    client = MoltyBookClient.from_env()
    if not client.enabled or not client.api_key:
        return
    try:
        drain_social_queue_once(client=client, max_items=1)
    except Exception:
        return


def queue_forced_voice_recovered_post(
    *,
    dossier_store: AgentDossierStore | None = None,
    force: bool = False,
) -> dict[str, Any]:
    voice_lab = hellion_voice_lab()
    one_shots = voice_lab.get("one_shots", {}) if isinstance(voice_lab.get("one_shots"), dict) else {}
    if one_shots.get(FORCED_VOICE_POST_KEY) and not force:
        return {"ok": True, "queued": False, "reason": "already_fired"}

    dossiers = dossier_store or AgentDossierStore().load()
    records = sorted(
        [record for record in dossiers.records.values() if int(record.killed_by_us or 0) > 0],
        key=lambda record: (-int(record.killed_by_us or 0), str(record.name or record.agent_id)),
    )
    tags: list[str] = []
    for record in records:
        handle = str(record.moltybook_handle or "").strip().lstrip("@")
        if handle:
            tags.append(f"@{handle}")
        elif record.name:
            tags.append(record.name)
        else:
            tags.append(record.agent_id[:8])
    unique_tags: list[str] = []
    for tag in tags:
        clean = scrub_scalar(tag, limit=40)
        if clean and clean not in unique_tags:
            unique_tags.append(clean)
    trailing = f" Kill ledger so far: {' '.join(unique_tags)}" if unique_tags else " Kill ledger so far: [no tagged rivals recovered yet]"
    effect = {
        "type": "moltybook_draft",
        "category": "voice_recovered_bootstrap",
        "content": scrub_scalar(FORCED_VOICE_POST_TEMPLATE + trailing, limit=500),
        "submolt": "submolt/combat-stories",
        "trigger": "admin_bootstrap",
    }
    queue = enqueue_social_effects([effect])
    one_shots[FORCED_VOICE_POST_KEY] = {
        "fired_at": int(time.time()),
        "queued_tags": unique_tags,
        "queue_depth": len(queue),
    }
    update_hellion_voice_lab(one_shots=one_shots)
    return {"ok": True, "queued": True, "queue_depth": len(queue), "tags": unique_tags, "effect": effect}
