from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from production_evaluation import append_sample, collect


def test_collects_sanitized_policy_and_postmortem_evidence() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "claw_runtime_status.json").write_text(
            json.dumps(
                {
                    "state": "playing",
                    "updated_at": 100,
                    "live_version": "1.13.1",
                    "action_audit": [
                        {"kind": "action_result", "outcome": {"ok": True}},
                        {"kind": "action_result", "outcome": {"ok": False, "code": "COOLDOWN_ACTIVE"}},
                        {"kind": "action_duplicate_suppressed"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / "policy_shadow.json").write_text(
            json.dumps({"records": [{"policy": {"outcome": "ALLOW"}, "enforced": False}]}), encoding="utf-8",
        )
        (root / "action_postmortems.json").write_text(
            json.dumps({"records": [{"failure_category": "target"}]}), encoding="utf-8",
        )
        with mock.patch("production_evaluation._loopback_health", return_value=True):
            report = collect(root)

    assert report["health_ok"] is True
    assert report["policy_outcomes"] == {"ALLOW": 1}
    assert report["postmortem_categories"] == {"target": 1}
    assert report["action_result_success_rate"] == 0.5
    assert report["cooldown_rejections_window"] == 1
    assert report["duplicate_actions_suppressed_window"] == 1
    assert report["credentials_collected"] is False
    assert report["policy_enforcement_active"] is False


def test_evaluation_series_is_bounded() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "series.json"
        for index in range(4):
            series = append_sample(path, {"recorded_at": index, "health_ok": index > 0}, limit=3)
    assert series["sample_count"] == 3
    assert series["first_recorded_at"] == 1
    assert series["health_success_rate"] == 1.0
