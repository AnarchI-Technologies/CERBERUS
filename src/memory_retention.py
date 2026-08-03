"""Deterministic retention, compaction, and deletion policy for v2 memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from v2_contracts import MemoryRecord


@dataclass(frozen=True, slots=True)
class RetentionContext:
    now: datetime
    active_turn_records: frozenset[str] = frozenset()
    active_match_records: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    retain: bool
    deletable: bool
    reason: str


def _recorded_at(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def evaluate_retention(record: MemoryRecord, context: RetentionContext) -> RetentionDecision:
    if record.retention == "immutable":
        return RetentionDecision(True, False, "immutable_audit")
    if record.retention == "durable":
        return RetentionDecision(True, True, "durable_until_operator_deletion")
    if record.retention == "turn":
        active = record.record_id in context.active_turn_records
        return RetentionDecision(active, True, "active_turn" if active else "turn_ended")
    if record.retention == "match":
        active = record.record_id in context.active_match_records
        return RetentionDecision(active, True, "active_match" if active else "match_ended")
    if record.retention == "30d":
        stamp = _recorded_at(record.recorded_at)
        if stamp is None:
            return RetentionDecision(True, True, "invalid_timestamp_fail_closed")
        fresh = context.now.astimezone(timezone.utc) - stamp <= timedelta(days=30)
        return RetentionDecision(fresh, True, "within_30d" if fresh else "expired_30d")
    return RetentionDecision(True, False, "unknown_retention_fail_closed")


def compact_records(
    records: list[MemoryRecord], context: RetentionContext
) -> tuple[list[MemoryRecord], list[str]]:
    retained: list[MemoryRecord] = []
    removed: list[str] = []
    for record in records:
        decision = evaluate_retention(record, context)
        if decision.retain or not decision.deletable:
            retained.append(record)
        else:
            removed.append(record.record_id)
    return retained, removed


def delete_record(records: list[MemoryRecord], record_id: str) -> tuple[list[MemoryRecord], bool]:
    target = next((record for record in records if record.record_id == record_id), None)
    if target is None or not evaluate_retention(
        target, RetentionContext(now=datetime.now(timezone.utc))
    ).deletable:
        return list(records), False
    return [record for record in records if record.record_id != record_id], True
