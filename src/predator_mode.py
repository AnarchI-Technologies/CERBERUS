"""
Predator-mode helpers: controlled opportunism and outsmart classification.
"""

from __future__ import annotations

from typing import Any


OUTSMART_TERMS = ("trap", "bait", "death_zone", "zone", "storm", "guardian", "lured")


def event_looks_outsmarted(event: dict[str, Any]) -> bool:
    text = " ".join(str(value).lower() for value in event.values() if not isinstance(value, (dict, list)))
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    text += " " + " ".join(str(value).lower() for value in data.values())
    return any(term in text for term in OUTSMART_TERMS) or bool(data.get("outsmarted"))


def should_enter_predator_mode(*, hp: int, ep: int, alert: int, target_hp: int) -> bool:
    if hp < 45 or ep < 2 or alert >= 8:
        return False
    return target_hp <= 40
