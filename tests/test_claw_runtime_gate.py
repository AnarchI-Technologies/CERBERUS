from __future__ import annotations

import sys
import os
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

import claw_runtime


class ClawRuntimeGameplayGateTests(unittest.TestCase):
    def test_clean_socket_close_records_terminal_connector_state(self) -> None:
        updates: list[dict[str, object]] = []

        class FakeSocket:
            def __aiter__(self):  # type: ignore[no-untyped-def]
                return self

            async def __anext__(self) -> str:
                raise StopAsyncIteration

        class FakeConnect:
            async def __aenter__(self) -> FakeSocket:
                return FakeSocket()

            async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
                return None

        config = claw_runtime.ClawRuntimeConfig(
            api_key="fixture-key",
            api_base="https://cdn.clawroyale.ai/api",
            version="1.13.1",
            mode="free",
            enabled=True,
        )
        with (
            mock.patch.object(claw_runtime, "discover_version", return_value="1.13.1"),
            mock.patch.object(claw_runtime, "record_account_balance", return_value={"ok": True}),
            mock.patch.object(claw_runtime, "record_stale_paid_waiting_games", return_value=[]),
            mock.patch.object(claw_runtime, "join_blocker_for_account", return_value=""),
            mock.patch.object(claw_runtime, "prejoin_loadout_report", return_value={"ok": True}),
            mock.patch.object(claw_runtime, "schedule_preseason1_claim_sweep"),
            mock.patch.object(claw_runtime, "read_json", return_value={}),
            mock.patch.object(claw_runtime, "update_status", side_effect=lambda **kwargs: updates.append(kwargs)),
            mock.patch.object(claw_runtime.websockets, "connect", return_value=FakeConnect()),
        ):
            asyncio.run(claw_runtime.connect_and_play(config, "/ws/join"))

        self.assertEqual(updates[0]["state"], "connecting")
        self.assertEqual(updates[1]["state"], "connected")
        self.assertEqual(updates[-1], {"state": "socket_closed", "last_error": "websocket closed without terminal game frame"})

    def test_default_paths_include_active_game_resume_endpoint(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(claw_runtime.websocket_paths(), ["/ws/join", "/ws/agent"])

    def test_server_cooldown_truth_closes_stale_snapshot_action_window(self) -> None:
        state = claw_runtime.TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {"self": {"id": "me", "hp": 100, "ep": 4}, "currentRegion": {"id": "r1"}},
            }
        )
        self.assertTrue(claw_runtime.server_action_window_open(state, None))
        self.assertTrue(claw_runtime.server_action_window_open(state, True))
        self.assertFalse(claw_runtime.server_action_window_open(state, False))

    def test_accepted_cooldown_result_recloses_out_of_order_ready_event(self) -> None:
        status = {"last_action": {"type": "use_item"}}
        self.assertTrue(claw_runtime.accepted_cooldown_action_result({"ok": True}, status))
        self.assertFalse(
            claw_runtime.accepted_cooldown_action_result(
                {"ok": False, "error": {"code": "COOLDOWN_ACTIVE"}}, status
            )
        )
        self.assertFalse(
            claw_runtime.accepted_cooldown_action_result(
                {"ok": True}, {"last_action": {"type": "pickup"}}
            )
        )

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

    def test_cooldown_remaining_blocks_even_when_can_act_is_true(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "cooldownRemainingMs": 15000,
            "view": {"canAct": True, "self": {"id": "me", "hp": 100, "ep": 10}},
        }

        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=True))

    def test_free_action_window_detects_weapon_upgrade_during_cooldown(self) -> None:
        snapshot = {
            "gameId": "game-1",
            "status": "running",
            "canAct": False,
            "cooldownRemainingMs": 15000,
            "view": {
                "self": {
                    "id": "me",
                    "hp": 100,
                    "ep": 10,
                    "equippedWeapon": {"typeId": "dagger"},
                    "inventory": [{"id": "blade-1", "typeId": "sword"}],
                },
                "currentRegion": {"id": "r1"},
            },
        }

        self.assertTrue(claw_runtime.has_free_action_window(claw_runtime.TurnState.from_snapshot(snapshot)))

    def test_dead_agent_never_gets_main_or_free_action_window(self) -> None:
        snapshot = {
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "view": {
                "self": {"id": "me", "hp": 0, "ep": 10, "isAlive": False},
                "currentRegion": {"id": "r1", "items": [{"id": "blade-1", "typeId": "sniper"}]},
            },
        }
        state = claw_runtime.TurnState.from_snapshot(snapshot)

        self.assertFalse(claw_runtime.wants_action(snapshot, snapshot, gameplay_ready=True))
        self.assertFalse(claw_runtime.has_free_action_window(state))

    def test_action_signature_is_turn_bound_and_reason_insensitive(self) -> None:
        first = claw_runtime.action_signature({"type": "move", "regionId": "r2", "reason": "one"}, turn=7)
        second = claw_runtime.action_signature({"type": "move", "regionId": "r2", "reason": "two"}, turn=7)
        third = claw_runtime.action_signature({"type": "move", "regionId": "r2", "reason": "two"}, turn=8)

        self.assertEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertTrue(claw_runtime.duplicate_action_sent({"last_action_turn": 7, "last_action_signature": first}, {"type": "move", "regionId": "r2"}, turn=7))
        self.assertFalse(claw_runtime.duplicate_action_sent({"last_action_turn": 7, "last_action_signature": first}, {"type": "move", "regionId": "r2"}, turn=8))

    def test_snapshot_signature_is_stable_for_identical_payloads(self) -> None:
        snapshot = {
            "turn": 9,
            "canAct": True,
            "view": {"self": {"id": "me", "hp": 100, "ep": 10}, "currentRegion": {"id": "r1"}},
        }
        copied = {
            "view": {"currentRegion": {"id": "r1"}, "self": {"ep": 10, "hp": 100, "id": "me"}},
            "canAct": True,
            "turn": 9,
        }

        self.assertEqual(claw_runtime.snapshot_signature(snapshot), claw_runtime.snapshot_signature(copied))

    def test_hollow_agent_view_does_not_trigger_action(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "view": {},
        }

        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=True))

    def test_agent_view_alias_fields_trigger_action(self) -> None:
        payload = {
            "type": "agent_view",
            "gameId": "game-1",
            "status": "running",
            "canAct": True,
            "view": {"agent": {"id": "me", "hp": 100, "ep": 10}, "region": {"id": "r1"}},
        }

        self.assertTrue(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload), gameplay_ready=True))

    def test_free_mode_auto_upgrades_to_ready_onchain_paid_room(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="free")
        welcome = {
            "type": "welcome",
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {"paidRoom": {"ok": True, "mode": {"offchain": False, "onchain": True}}},
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "paid-ready-1",
                    "playerCount": 29,
                    "requiredPlayers": 30,
                }
            ],
        }

        self.assertEqual(
            claw_runtime.hello_frame(config, welcome),
            {"type": "hello", "entryType": "paid", "mode": "onchain"},
        )

    def test_free_mode_does_not_auto_upgrade_to_unproven_paid_room(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="free")
        welcome = {
            "type": "welcome",
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {"paidRoom": {"ok": True, "mode": {"onchain": True}}},
        }

        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})

    def test_paid_only_decision_never_gets_rewritten_as_free(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "type": "welcome",
            "decision": "PAID_ONLY",
            "readiness": {"paidRoom": {"ok": True, "mode": {"offchain": True}}},
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "paid-not-ready",
                    "playerCount": 3,
                    "requiredPlayers": 10,
                }
            ],
        }

        self.assertIsNone(claw_runtime.hello_frame(config, welcome))
        self.assertIsNone(claw_runtime.hello_frame(config, welcome, paid_account_ready=False))

    def test_recent_paid_join_failure_keeps_free_mode_temporarily(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="free")
        welcome = {
            "type": "welcome",
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {"paidRoom": {"ok": True, "mode": {"onchain": True}}},
        }
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        old_cooldown = os.environ.get("CLAW_ROYALE_PAID_RETRY_COOLDOWN_SECONDS")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                os.environ["CLAW_ROYALE_PAID_RETRY_COOLDOWN_SECONDS"] = "600"
                claw_runtime.update_status(last_error="SERVICE_UNAVAILABLE: join onchain failed")

                self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir
            if old_cooldown is None:
                os.environ.pop("CLAW_ROYALE_PAID_RETRY_COOLDOWN_SECONDS", None)
            else:
                os.environ["CLAW_ROYALE_PAID_RETRY_COOLDOWN_SECONDS"] = old_cooldown


if __name__ == "__main__":
    unittest.main()
