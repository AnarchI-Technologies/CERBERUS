"""Compile match evidence into retained deterministic lessons."""

from __future__ import annotations

from collections import Counter
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_dossiers import AgentDossierStore
from external_wisdom import validated_strategy_wisdom
from longterm_memory import LongTermMemoryStore
from memory_system import CompactMemoryStore, stable_hash, utc_now
from runtime_state import match_evidence


def _action_key(entry: dict[str, Any]) -> str:
    action = entry.get("action") if isinstance(entry.get("action"), dict) else {}
    action_type = str(action.get("type") or "unknown")
    target = action.get("targetId") or action.get("itemId") or action.get("regionId") or ""
    return f"{action_type}:{target}" if target else action_type


def _state_key(entry: dict[str, Any]) -> str:
    state = entry.get("state") if isinstance(entry.get("state"), dict) else {}
    terrain = str(state.get("terrain") or "unknown").lower()
    death = "deathzone" if state.get("death_zone") else "safe"
    visible = f"a{state.get('visible_agents') or 0}m{state.get('visible_monsters') or 0}i{state.get('visible_items') or 0}"
    return f"{terrain}:{death}:{visible}"


def _outcome_key(entry: dict[str, Any]) -> str:
    outcome = entry.get("outcome") if isinstance(entry.get("outcome"), dict) else {}
    if outcome.get("ok") is True:
        return "accepted"
    message = str(outcome.get("message") or outcome.get("code") or "").lower()
    if "range" in message:
        return "range_reject"
    if "cooldown" in message:
        return "cooldown_reject"
    if "blocked" in message:
        return "blocked_reject"
    if message:
        return "reject"
    return "unknown"


def compile_lessons(
    *,
    evidence_limit: int = 500,
    memory: CompactMemoryStore | None = None,
    longterm: LongTermMemoryStore | None = None,
    dossiers: AgentDossierStore | None = None,
    min_count: int = 2,
) -> dict[str, Any]:
    rows = match_evidence(limit=evidence_limit)
    action_counts: Counter[str] = Counter()
    reject_counts: Counter[str] = Counter()
    terrain_counts: Counter[str] = Counter()
    lessons: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, dict):
            continue
        action_key = _action_key(row)
        state_key = _state_key(row)
        outcome_key = _outcome_key(row)
        action_counts[action_key] += 1
        terrain_counts[state_key] += 1
        if outcome_key.endswith("reject"):
            reject_counts[f"{action_key}|{outcome_key}"] += 1

    for key, count in reject_counts.most_common(12):
        if count < min_count:
            continue
        action_key, outcome_key = key.split("|", 1)
        lessons.append(
            {
                "domain": "runtime",
                "key": f"reject:{action_key}:{outcome_key}",
                "text": f"lesson: {action_key} repeatedly hit {outcome_key}; require fresh board evidence before repeating",
                "confidence": 0.88,
                "importance": 82,
                "count": count,
            }
        )

    for key, count in action_counts.most_common(12):
        if count < min_count:
            continue
        action_type = key.split(":", 1)[0]
        if action_type in {"pickup", "equip", "use_item", "move", "attack"}:
            lessons.append(
                {
                    "domain": "policy",
                    "key": f"pattern:{key}",
                    "text": f"lesson: {key} appeared {count} times in recent evidence; keep its preconditions visible in scoring",
                    "confidence": 0.66,
                    "importance": 58,
                    "count": count,
                }
            )

    for key, count in terrain_counts.most_common(8):
        if count < min_count:
            continue
        lessons.append(
            {
                "domain": "map",
                "key": f"terrain:{key}",
                "text": f"lesson: terrain pattern {key} recurred {count} times; dashboard map should keep it easy to inspect",
                "confidence": 0.6,
                "importance": 45,
                "count": count,
                }
            )

    if dossiers is not None:
        for record in dossiers.records.values():
            killed_us = int(getattr(record, "killed_us", 0) or 0)
            killed_by_us = int(getattr(record, "killed_by_us", 0) or 0)
            helpful = int(getattr(record, "helpful_messages", 0) or 0)
            alliance_score = int(getattr(record, "alliance_score", 0) or 0)
            betrayed_us = int(getattr(record, "betrayed_us", 0) or 0)
            name = str(getattr(record, "name", "") or getattr(record, "agent_id", "")[:8] or "rival")
            if killed_us >= min_count:
                lessons.append(
                    {
                        "domain": "dossiers",
                        "key": f"dossier:repeat_killer:{record.agent_id}",
                        "text": f"lesson: {name} has repeatedly eliminated us; weight failure memory and shared counterplay above victory laps until the pattern breaks",
                        "confidence": 0.84,
                        "importance": 78,
                        "count": killed_us,
                    }
                )
            if killed_by_us >= min_count and killed_us == 0:
                lessons.append(
                    {
                        "domain": "dossiers",
                        "key": f"dossier:repeat_prey:{record.agent_id}",
                        "text": f"lesson: {name} repeatedly folds under pressure; keep the cross-agent dossier hot and press favorable fights when the board still agrees",
                        "confidence": 0.76,
                        "importance": 64,
                        "count": killed_by_us,
                    }
                )
            if helpful >= min_count and alliance_score >= 4:
                lessons.append(
                    {
                        "domain": "alliances",
                        "key": f"alliance:useful_contact:{record.agent_id}",
                        "text": f"lesson: {name} has delivered repeated useful communication; preserve the alliance until betrayal math clearly wins",
                        "confidence": 0.74,
                        "importance": 52,
                        "count": helpful,
                    }
                )
            if betrayed_us >= 1:
                lessons.append(
                    {
                        "domain": "alliances",
                        "key": f"alliance:betrayed_us:{record.agent_id}",
                        "text": f"lesson: {name} broke a useful alliance; future trust should demand stronger proof than one good handoff",
                        "confidence": 0.9,
                        "importance": 81,
                        "count": betrayed_us,
                    }
                )

    for item in validated_strategy_wisdom():
        rendered = str(item.get("rendered_lesson") or "").strip()
        if not rendered:
            continue
        lessons.append(
            {
                "domain": "wisdom",
                "key": f"wisdom:{item['key']}",
                "text": f"lesson: {rendered}",
                "confidence": 0.72,
                "importance": 60,
                "count": 1,
            }
        )

    unique_lessons = _dedupe_lessons(lessons)[:24]
    memory_store = memory or CompactMemoryStore().load()
    longterm_store = longterm
    for lesson in unique_lessons:
        memory_store.remember_lesson(
            lesson["domain"],
            lesson["text"],
            source="lesson_compiler",
            confidence=str(lesson["confidence"]),
        )
        if longterm_store is not None:
            longterm_store.remember(
                kind="lesson",
                scope="claw_royale",
                key=lesson["key"],
                text=lesson["text"],
                confidence=float(lesson["confidence"]),
                importance=int(lesson["importance"]),
                metadata={"count": lesson["count"], "compiled_at": utc_now()},
            )
    if unique_lessons:
        memory_store.save()
    return {
        "compiled_at": utc_now(),
        "evidence_rows": len(rows),
        "lesson_count": len(unique_lessons),
        "lessons": unique_lessons,
        "source_hash": stable_hash(rows[-100:], length=24),
        "external_wisdom_keys": [str(item.get("key") or "") for item in validated_strategy_wisdom()],
    }


def _dedupe_lessons(lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for lesson in lessons:
        key = str(lesson.get("key") or lesson.get("text") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(lesson)
    return out
