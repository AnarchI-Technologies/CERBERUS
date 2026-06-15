"""Settlement lesson extraction.

The parts-bin bot kept useful post-game lessons, but mixed them with old
deployment and optional LLM analysis. This module keeps only deterministic,
compact lessons that can feed CERBERUS memory without storing raw logs.
"""

from __future__ import annotations

from typing import Any

from memory_system import CompactMemoryStore


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def _reward_value(rewards: dict[str, Any], *keys: str) -> int:
    for key in keys:
        if key in rewards:
            return _as_int(rewards.get(key))
    return 0


def _settlement_payload(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(snapshot, dict):
        return None
    candidates: list[dict[str, Any]] = []
    for event in snapshot.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or event.get("eventType") or "").lower()
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        if event_type in {"game_settled", "game_ended"} and isinstance(data, dict):
            candidates.append(data.get("settlement") if isinstance(data.get("settlement"), dict) else data)
    for key in ("settlement", "gameResult", "result"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            candidates.append(value)
    return candidates[-1] if candidates else None


def settlement_lessons(snapshot: dict[str, Any]) -> list[str]:
    payload = _settlement_payload(snapshot)
    if not payload:
        return []
    result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    rewards = result.get("rewards") if isinstance(result.get("rewards"), dict) else {}
    final_rank = _as_int(result.get("finalRank") or result.get("rank"))
    kills = _as_int(result.get("kills"))
    smoltz = _reward_value(rewards, "sMoltz", "smoltz", "balance")
    won = bool(result.get("isWinner") or result.get("winner") is True or final_rank == 1)

    lessons: list[str] = []
    if won:
        lessons.append(f"settlement: win with kills={kills}; preserve winning pace")
    elif final_rank and final_rank <= 3:
        lessons.append(f"settlement: top-three rank={final_rank}; survival path is viable")
    if kills == 0 and final_rank > 20:
        lessons.append("settlement: zero kills with early low-pressure exit; seek weapon/value sooner")
    elif kills == 0:
        lessons.append("settlement: zero kills; increase guardian or wounded-agent pressure")
    if kills >= 5:
        lessons.append(f"settlement: striker game with kills={kills}; combat posture can stay aggressive")
    if smoltz >= 250:
        lessons.append(f"settlement: high yield {smoltz} sMoltz; intercept/value route was productive")
    elif smoltz == 0 and not won:
        lessons.append("settlement: no reward; prioritize pickups, survival, and killable targets")
    if final_rank > 30:
        lessons.append(f"settlement: rank {final_rank}; reduce blind exploration and exit death-zone risk earlier")
    if not lessons:
        lessons.append("settlement: game completed; keep compact outcome for trend memory")
    return list(dict.fromkeys(lessons))


def remember_settlement_lessons(snapshot: dict[str, Any], memory: CompactMemoryStore) -> list[str]:
    lessons = settlement_lessons(snapshot)
    for lesson in lessons:
        memory.remember_lesson("claw_royale", lesson, source="settlement", confidence="observed")
    return lessons
