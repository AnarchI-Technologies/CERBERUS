"""Conservative PreSeason 1 quest-claim discovery and execution.

Season progress accrues when a match finalizes, but reached stepped and daily
quests still need an explicit claim.  The API can add fields without warning,
so this module only acts on records that explicitly say they are claimable,
reached, or completed.  Unknown shapes fail closed instead of guessing tiers.
"""

from __future__ import annotations

import re
from typing import Any


QUEST_KEY_FIELDS = ("key", "questKey", "quest_key")
TIER_FIELDS = ("tier", "tierNumber", "tier_number")
CLAIMABLE_FIELDS = ("claimable", "canClaim", "can_claim", "reached", "completed")
CLAIMED_FIELDS = ("claimed", "isClaimed", "is_claimed")
CLAIMABLE_STATUSES = {"claimable", "reached", "completed", "ready"}
GENERIC_CONTAINER_KEYS = {
    "data",
    "items",
    "quests",
    "dailyquests",
    "daily_quests",
    "tracks",
    "tiers",
    "results",
    "value",
}
QUEST_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
LEVEL_FIELDS = ("level", "currentLevel", "current_level", "currentTier", "current_tier")
PROGRESS_FIELDS = ("progress", "current", "count", "amount")
TARGET_FIELDS = ("target", "threshold", "required", "nextTarget", "next_target")


def _quest_key(record: dict[str, Any], inherited: str = "") -> str:
    for field in QUEST_KEY_FIELDS:
        value = str(record.get(field) or "").strip()
        if QUEST_KEY_RE.fullmatch(value):
            return value
    return inherited if QUEST_KEY_RE.fullmatch(inherited) else ""


def _tier(record: dict[str, Any]) -> int | None:
    for field in TIER_FIELDS:
        value = record.get(field)
        if isinstance(value, bool):
            continue
        try:
            tier = int(value)
        except (TypeError, ValueError):
            continue
        if tier > 0:
            return tier
    return None


def _is_claimed(record: dict[str, Any]) -> bool:
    return any(record.get(field) is True for field in CLAIMED_FIELDS)


def _is_explicitly_claimable(record: dict[str, Any]) -> bool:
    if any(record.get(field) is True for field in CLAIMABLE_FIELDS):
        return True
    return str(record.get("status") or "").strip().lower() in CLAIMABLE_STATUSES


def _looks_like_quest_record(record: dict[str, Any]) -> bool:
    markers = set(record)
    return bool(
        markers.intersection(QUEST_KEY_FIELDS)
        or markers.intersection(TIER_FIELDS)
        or markers.intersection(CLAIMABLE_FIELDS)
        or markers.intersection(CLAIMED_FIELDS)
        or "tiers" in markers
        or "progress" in markers
    )


def _walk_records(value: Any, inherited_key: str = "") -> list[tuple[dict[str, Any], str]]:
    records: list[tuple[dict[str, Any], str]] = []
    if isinstance(value, list):
        for item in value:
            records.extend(_walk_records(item, inherited_key))
        return records
    if not isinstance(value, dict):
        return records

    current_key = _quest_key(value, inherited_key)
    records.append((value, current_key))
    for field, child in value.items():
        child_key = current_key
        normalized_field = str(field).strip()
        if (
            not child_key
            and QUEST_KEY_RE.fullmatch(normalized_field)
            and normalized_field.lower() not in GENERIC_CONTAINER_KEYS
            and isinstance(child, dict)
            and _looks_like_quest_record(child)
        ):
            child_key = normalized_field
        if isinstance(child, (dict, list)):
            records.extend(_walk_records(child, child_key))
    return records


def stepped_claim_candidates(payload: Any) -> list[tuple[str, int]]:
    candidates: set[tuple[str, int]] = set()
    for record, inherited_key in _walk_records(payload):
        key = _quest_key(record, inherited_key)
        tier = _tier(record)
        if key and tier and _is_explicitly_claimable(record) and not _is_claimed(record):
            candidates.add((key, tier))
    return sorted(candidates)


def daily_claim_candidates(payload: Any) -> list[str]:
    candidates: set[str] = set()
    for record, inherited_key in _walk_records(payload):
        key = _quest_key(record, inherited_key)
        if key and _is_explicitly_claimable(record) and not _is_claimed(record):
            candidates.add(key)
    return sorted(candidates)


def _number(record: dict[str, Any], fields: tuple[str, ...]) -> int | float | None:
    for field in fields:
        value = record.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        return value
    return None


def objective_progress_snapshot(payload: Any) -> list[dict[str, Any]]:
    """Return a small identity-free view of objective progress."""

    snapshots: dict[str, dict[str, Any]] = {}
    for record, inherited_key in _walk_records(payload):
        key = _quest_key(record, inherited_key)
        if not key:
            continue
        item: dict[str, Any] = {"key": key}
        level = _number(record, LEVEL_FIELDS)
        progress = _number(record, PROGRESS_FIELDS)
        target = _number(record, TARGET_FIELDS)
        tier = _tier(record)
        status = str(record.get("status") or "").strip().lower()
        if level is not None:
            item["level"] = level
        if progress is not None:
            item["progress"] = progress
        if target is not None:
            item["target"] = target
        if tier is not None:
            item["tier"] = tier
        if status in CLAIMABLE_STATUSES or status in {"claimed", "locked", "active", "in_progress"}:
            item["status"] = status
        if _is_claimed(record):
            item["claimed"] = True
        if _is_explicitly_claimable(record) and not _is_claimed(record):
            item["claimable"] = True
        if len(item) == 1:
            continue
        prior = snapshots.get(key)
        if prior is None or len(item) > len(prior):
            snapshots[key] = item
    return [snapshots[key] for key in sorted(snapshots)]


def claim_reached_preseason1_points(
    client: Any,
    *,
    stepped_payload: Any,
    daily_payload: Any,
) -> dict[str, Any]:
    """Claim only explicitly reached records; continue if one claim fails."""

    stepped = stepped_claim_candidates(stepped_payload)
    daily = daily_claim_candidates(daily_payload)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for key, tier in stepped:
        try:
            response = client.claim_preseason1_quest(key, tier)
            results.append(
                {
                    "kind": "stepped",
                    "key": key,
                    "tier": tier,
                    "claimed": bool(response.get("claimed", True)) if isinstance(response, dict) else True,
                }
            )
        except Exception as exc:
            errors.append({"kind": "stepped", "key": key, "tier": tier, "error": str(exc)[:240]})

    for key in daily:
        try:
            response = client.claim_preseason1_daily_quest(key)
            results.append(
                {
                    "kind": "daily",
                    "key": key,
                    "claimed": bool(response.get("claimed", True)) if isinstance(response, dict) else True,
                }
            )
        except Exception as exc:
            errors.append({"kind": "daily", "key": key, "error": str(exc)[:240]})

    return {
        "ok": not errors,
        "stepped_candidates": [{"key": key, "tier": tier} for key, tier in stepped],
        "daily_candidates": daily,
        "attempted": len(stepped) + len(daily),
        "newly_claimed": sum(1 for item in results if item.get("claimed")),
        "results": results,
        "errors": errors,
        "progress": objective_progress_snapshot(stepped_payload),
        "daily_progress": objective_progress_snapshot(daily_payload),
    }
