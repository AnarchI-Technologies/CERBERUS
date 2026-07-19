from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from memory_retention import RetentionContext, compact_records, delete_record, evaluate_retention
from v2_contracts import MemoryRecord


NOW = datetime(2026, 7, 19, tzinfo=timezone.utc)


def record(record_id: str, retention: str, recorded_at: str = "2026-07-18T00:00:00Z") -> MemoryRecord:
    return MemoryRecord(
        record_id=record_id,
        classification="audit" if retention == "immutable" else "knowledge",
        source_ref="test:retention",
        recorded_at=recorded_at,
        content="safe",
        retention=retention,
    )


def test_turn_and_match_records_expire_outside_active_scope() -> None:
    context = RetentionContext(
        now=NOW,
        active_turn_records=frozenset({"turn-live"}),
        active_match_records=frozenset({"match-live"}),
    )
    assert evaluate_retention(record("turn-live", "turn"), context).retain
    assert not evaluate_retention(record("turn-old", "turn"), context).retain
    assert evaluate_retention(record("match-live", "match"), context).retain
    assert not evaluate_retention(record("match-old", "match"), context).retain


def test_thirty_day_retention_expires_only_old_valid_timestamps() -> None:
    context = RetentionContext(now=NOW)
    assert evaluate_retention(record("fresh", "30d", "2026-07-01T00:00:00Z"), context).retain
    assert not evaluate_retention(record("old", "30d", "2026-05-01T00:00:00Z"), context).retain
    invalid = evaluate_retention(record("invalid", "30d", "not-a-date"), context)
    assert invalid.retain
    assert invalid.reason == "invalid_timestamp_fail_closed"


def test_durable_is_operator_deletable_but_immutable_is_not() -> None:
    records = [record("durable", "durable"), record("audit", "immutable")]
    after_durable, deleted = delete_record(records, "durable")
    assert deleted
    assert [item.record_id for item in after_durable] == ["audit"]

    unchanged, deleted = delete_record(records, "audit")
    assert not deleted
    assert unchanged == records


def test_compaction_removes_only_expired_deletable_records() -> None:
    records = [
        record("turn-old", "turn"),
        record("match-old", "match"),
        record("old", "30d", "2026-05-01T00:00:00Z"),
        record("durable", "durable"),
        record("audit", "immutable"),
    ]
    retained, removed = compact_records(records, RetentionContext(now=NOW))

    assert [item.record_id for item in retained] == ["durable", "audit"]
    assert removed == ["turn-old", "match-old", "old"]
