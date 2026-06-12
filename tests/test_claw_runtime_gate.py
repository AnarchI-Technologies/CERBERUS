from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

import claw_runtime


class ClawRuntimeGameplayGateTests(unittest.TestCase):
    def test_agent_view_waiting_status_does_not_act(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "waiting",
            "canAct": True,
            "view": {"self": {"id": "me", "hp": 100, "ep": 10}},
        }

        self.assertEqual(claw_runtime.game_status(payload, claw_runtime.unwrap_snapshot(payload)), "waiting")
        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=False))

    def test_agent_view_running_status_allows_action(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "view": {"self": {"id": "me", "hp": 100, "ep": 10}},
        }

        self.assertEqual(claw_runtime.game_status(payload, claw_runtime.unwrap_snapshot(payload)), "running")
        self.assertTrue(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=False))

    def test_turn_advanced_without_status_requires_prior_running_frame(self) -> None:
        payload = {
            "type": "turn_advanced",
            "gameId": "game-1",
            "view": {"self": {"id": "me", "hp": 100, "ep": 10}},
        }
        snapshot = claw_runtime.unwrap_snapshot(payload)

        self.assertEqual(claw_runtime.game_status(payload, snapshot), "")
        self.assertFalse(claw_runtime.wants_action(payload, snapshot, gameplay_ready=False))
        self.assertTrue(claw_runtime.wants_action(payload, snapshot, gameplay_ready=True))

    def test_can_act_false_still_blocks_after_running(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": False,
            "view": {"canAct": False, "self": {"id": "me", "hp": 100, "ep": 10}},
        }

        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=True))

    def test_hollow_agent_view_does_not_trigger_action(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "view": {},
        }

        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=True))


if __name__ == "__main__":
    unittest.main()
