from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

import claw_runtime
from action_postmortem import build_action_postmortem
from runtime_state import action_postmortems


def test_deterministic_postmortem_classifies_and_bounds_experiment() -> None:
    record = build_action_postmortem(
        action={"type": "move", "regionId": "r2", "reason": "escape pressure"},
        payload={"success": False, "error": "TARGET_BLOCKED"},
        snapshot={"game_id": "g1", "turn": 9},
    )

    assert "move should be accepted" in record["expectation"]
    assert record["observed_outcome"] == "rejected move: TARGET_BLOCKED"
    assert record["failure_category"] == "target"
    assert record["confidence"] == 0.92
    assert record["proposed_experiment"]["sample_size"] <= 20
    assert "observation_only" in record["proposed_experiment"]["guardrails"]
    assert record["activated"] is False


def test_runtime_records_postmortem_without_changing_action_policy() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("CERBERUS_MEMORY_DIR")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            status = {
                "current_game_id": "game-7",
                "last_action": {"type": "move", "regionId": "r2", "reason": "escape pressure"},
                "last_snapshot": {"game_id": "game-7", "turn": 14, "hp": 44, "max_hp": 100},
            }
            claw_runtime.record_action_result_learning(
                {"type": "action_result", "success": False, "error": "TARGET_BLOCKED"},
                status=status,
            )
            records = action_postmortems()
        finally:
            if old is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old

    assert len(records) == 1
    assert records[0]["failure_category"] == "target"
    assert records[0]["activated"] is False
