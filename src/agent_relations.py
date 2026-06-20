"""Deterministic alliance and betrayal math for encountered agents."""

from __future__ import annotations

from typing import Any


def alliance_value(record: Any) -> int:
    if record is None:
        return 0
    helpful = int(getattr(record, "helpful_messages", 0) or 0)
    truthful = int(getattr(record, "truthful_messages", 0) or 0)
    offers = int(getattr(record, "alliance_offers", 0) or 0)
    validated = len(getattr(record, "validated_strats", []) or [])
    score = int(getattr(record, "alliance_score", 0) or 0)
    betrayed_us = int(getattr(record, "betrayed_us", 0) or 0)
    killed_us = int(getattr(record, "killed_us", 0) or 0)
    return max(
        0,
        score + (helpful * 2) + truthful + offers + min(validated, 3) - (betrayed_us * 8) - (killed_us * 4),
    )


def is_allied(record: Any, *, threshold: int = 6) -> bool:
    if record is None:
        return False
    if int(getattr(record, "betrayed_us", 0) or 0) > 0:
        return False
    return alliance_value(record) >= threshold


def betrayal_reward(record: Any, state: Any, agent: Any) -> int:
    reward = 0
    tendencies = {str(item).lower() for item in getattr(record, "observed_tendencies", []) or []}
    high_value = any(marker in tendency for marker in ("collects_smoltz", "collects_high_value_loot", "collects_loadout") for tendency in tendencies)
    low_hp = int(getattr(agent, "hp", 0) or 0) and int(getattr(agent, "hp", 0) or 0) <= 35
    if high_value:
        reward += 4
    if low_hp:
        reward += 3
    if high_value and low_hp:
        reward += 3
    if int(getattr(record, "killed_by_us", 0) or 0) > 0:
        reward += 2
    if int(getattr(record, "betrayed_us", 0) or 0) > 0:
        reward += 8
    if getattr(state, "alert_active", False):
        reward += 1
    terrain = str(getattr(getattr(state, "current_region", None), "terrain", "") or "").lower()
    name = str(getattr(getattr(state, "current_region", None), "name", "") or "").lower()
    if "ruin" in terrain or "ruin" in name:
        reward += 2
    if (int(getattr(state.self, "hp", 0) or 0) / max(1, int(getattr(state.self, "max_hp", 100) or 100))) >= 0.7:
        reward += 1
    if int(getattr(state.self, "ep", 0) or 0) >= 3:
        reward += 1
    return reward


def should_betray(record: Any, state: Any, agent: Any, *, margin: int = 3) -> bool:
    return is_allied(record) and betrayal_reward(record, state, agent) >= alliance_value(record) + margin
