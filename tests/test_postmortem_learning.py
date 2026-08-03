from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from postmortem_learning import build_postmortem, record_postmortem


class FakeStore:
    def __init__(self) -> None:
        self.calls = []

    def remember(self, **kwargs):
        self.calls.append(kwargs)
        return "memory-1"


class PostmortemLearningTests(unittest.TestCase):
    def test_negative_delta_creates_bounded_economy_experiment(self) -> None:
        record = build_postmortem(
            game_id="game-1",
            last_action={"type": "move", "reason": "move toward safer loot"},
            balance_delta=-1.25,
        )

        self.assertEqual(record["expectation"], "move toward safer loot")
        self.assertEqual(record["likely_failure_category"], "economy")
        self.assertEqual(record["confidence"], 0.72)
        self.assertEqual(record["proposed_experiment"]["sample_size"], 10)
        self.assertFalse(record["proposed_experiment"]["changes_live_policy"])

    def test_rejection_evidence_takes_priority_over_generic_terminal_signal(self) -> None:
        record = build_postmortem(
            game_id="game-2",
            last_action={"type": "attack"},
            terminal_error="game ended",
            action_audit=[{"kind": "action_result", "outcome": {"ok": False}}],
        )

        self.assertEqual(record["likely_failure_category"], "action_rejection")
        self.assertGreaterEqual(record["confidence"], 0.7)

    def test_sparse_evidence_proposes_collection_without_policy_change(self) -> None:
        record = build_postmortem(game_id="game-3", last_action=None)

        self.assertEqual(record["likely_failure_category"], "insufficient_evidence")
        self.assertEqual(record["proposed_experiment"]["key"], "evidence_collection")
        self.assertFalse(record["proposed_experiment"]["changes_live_policy"])

    def test_record_uses_typed_longterm_memory_and_preserves_metadata(self) -> None:
        store = FakeStore()
        record = build_postmortem(game_id="game-4", last_action={"type": "rest"})

        item_id = record_postmortem(record, store=store)

        self.assertEqual(item_id, "memory-1")
        self.assertEqual(store.calls[0]["kind"], "postmortem")
        self.assertEqual(store.calls[0]["scope"], "claw_royale")
        self.assertEqual(store.calls[0]["metadata"], record)

    def test_terminal_evidence_is_bounded_and_preserved(self) -> None:
        record = build_postmortem(
            game_id="game-5",
            last_action={"type": "attack", "reason": "finish low target"},
            terminal_evidence={
                "placement": 12,
                "killer_id": "rival-1",
                "killer_name": "Rival",
                "final_hp": 0,
                "final_ep": 2,
                "alive": False,
                "alive_count": 11,
                "recent_actions": [{"type": f"action-{index}"} for index in range(8)],
            },
        )

        self.assertEqual(record["evidence"]["placement"], 12)
        self.assertEqual(record["evidence"]["killer_id"], "rival-1")
        self.assertEqual(record["evidence"]["final_hp"], 0)
        self.assertFalse(record["evidence"]["alive"])
        self.assertEqual(len(record["evidence"]["recent_actions"]), 5)


if __name__ == "__main__":
    unittest.main()
