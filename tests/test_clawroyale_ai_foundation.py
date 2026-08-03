from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from clawroyale_ai import (
    ACTION_CAPABILITY,
    ADAPTER_ID,
    ClawRoyaleAdapter,
    StrategyError,
    StrategyRegistry,
    validate_action,
)
import interoperability
from interoperability import Adapter, AdapterRegistry, Command, Observation, ResultStatus, encode


class FakeTransport:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {"success": True, "code": "accepted"}
        self.frames: list[dict] = []

    async def __call__(self, frame: dict) -> dict:
        self.frames.append(frame)
        return dict(self.response)


def action_command(action: dict, command_id: str = "c1") -> Command:
    return Command(
        command_id=command_id,
        adapter_id=ADAPTER_ID,
        capability=ACTION_CAPABILITY,
        session_id="session-1",
        payload={"action": action},
        correlation_id="corr-1",
    )


class ClawRoyaleAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_satisfies_generic_protocol(self) -> None:
        self.assertIsInstance(ClawRoyaleAdapter(FakeTransport()), Adapter)

    async def test_adapter_registers_in_generic_registry(self) -> None:
        registry = AdapterRegistry()
        registry.register(ClawRoyaleAdapter(FakeTransport()))
        self.assertEqual(registry.descriptors()[0].adapter_id, ADAPTER_ID)

    async def test_action_submission_uses_injected_transport(self) -> None:
        transport = FakeTransport()
        result = await ClawRoyaleAdapter(transport).invoke(
            action_command({"type": "attack", "targetId": "rival"})
        )
        self.assertEqual(result.status, ResultStatus.SUCCEEDED)
        self.assertEqual(
            transport.frames,
            [{"type": "action", "action": {"type": "attack", "targetId": "rival"}}],
        )

    async def test_invalid_action_fails_closed_without_transport(self) -> None:
        transport = FakeTransport()
        result = await ClawRoyaleAdapter(transport).invoke(action_command({"type": "attack"}))
        self.assertEqual(result.status, ResultStatus.REJECTED)
        self.assertEqual(result.error_code, "missing:targetId")
        self.assertEqual(transport.frames, [])

    async def test_retryable_failure_is_normalized(self) -> None:
        transport = FakeTransport({"success": False, "code": "TIMEOUT", "error": "late"})
        result = await ClawRoyaleAdapter(transport).invoke(action_command({"type": "rest"}))
        self.assertEqual(result.status, ResultStatus.FAILED)
        self.assertTrue(result.retryable)

    async def test_join_validation_fails_closed(self) -> None:
        transport = FakeTransport()
        command = Command("join", ADAPTER_ID, "clawroyale.session.join", "s", {"entry_type": "x"})
        result = await ClawRoyaleAdapter(transport).invoke(command)
        self.assertEqual(result.status, ResultStatus.REJECTED)
        self.assertEqual(transport.frames, [])

    async def test_leave_maps_session_without_provider_state(self) -> None:
        transport = FakeTransport()
        command = Command("leave", ADAPTER_ID, "clawroyale.session.leave", "session-9")
        await ClawRoyaleAdapter(transport).invoke(command)
        self.assertEqual(transport.frames, [{"type": "leave", "sessionId": "session-9"}])


class ClawRoyaleObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = ClawRoyaleAdapter(FakeTransport())
        self.fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "claw_agent_view_sanitized.json").read_text(
                encoding="utf-8"
            )
        )

    def test_sanitized_replay_becomes_json_only_observation(self) -> None:
        observation = self.adapter.normalize_observation(
            self.fixture,
            session_id="s",
            sequence=12,
            observation_id="o",
        )
        self.assertIsInstance(observation, Observation)
        self.assertEqual(observation.payload["match_id"], "fixture-game")
        self.assertEqual(observation.payload["location"]["terrain"], "ruin")
        self.assertEqual(observation.payload["actor"]["id"], "fixture-agent")

    def test_normalization_uses_allowlisted_fields(self) -> None:
        payload = json.loads(json.dumps(self.fixture))
        payload["view"]["self"]["apiKey"] = "secret"
        payload["view"]["wallet"] = "0xprivate"
        observation = self.adapter.normalize_observation(
            payload, session_id="s", sequence=0, observation_id="o"
        )
        rendered = encode(observation).lower()
        self.assertNotIn("apikey", rendered)
        self.assertNotIn("wallet", rendered)

    def test_available_actions_are_sorted(self) -> None:
        payload = json.loads(json.dumps(self.fixture))
        payload["availableActions"] = {"rest": {}, "attack": {}}
        observation = self.adapter.normalize_observation(
            payload, session_id="s", sequence=0, observation_id="o"
        )
        self.assertEqual(observation.payload["available_actions"], ("attack", "rest"))

    def test_non_object_observation_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.adapter.normalize_observation(
                None, session_id="s", sequence=0, observation_id="o"
            )


class ClawRoyaleStrategyRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.observation = ClawRoyaleAdapter(FakeTransport()).normalize_observation(
            {"type": "agent_view", "canAct": True},
            session_id="s",
            sequence=1,
            observation_id="o",
        )

    def test_registry_selects_highest_priority_proposal(self) -> None:
        class Low:
            strategy_id = "low"
            priority = 1

            def propose(self, observation: Observation) -> Command:
                return Command("low", ADAPTER_ID, ACTION_CAPABILITY, observation.session_id, {"action": {"type": "rest"}})

        class High:
            strategy_id = "high"
            priority = 10

            def propose(self, observation: Observation) -> Command:
                return Command("high", ADAPTER_ID, ACTION_CAPABILITY, observation.session_id, {"action": {"type": "rest"}})

        registry = StrategyRegistry()
        registry.register(Low())
        registry.register(High())
        selection = registry.select(self.observation)
        self.assertEqual(selection.strategy_id, "high")

    def test_registry_falls_through_none_proposals(self) -> None:
        class Skip:
            strategy_id = "skip"
            priority = 10

            def propose(self, observation: Observation) -> None:
                return None

        registry = StrategyRegistry()
        registry.register(Skip())
        self.assertIsNone(registry.select(self.observation))

    def test_duplicate_strategy_is_rejected(self) -> None:
        class Skip:
            strategy_id = "skip"
            priority = 1

            def propose(self, observation: Observation) -> None:
                return None

        registry = StrategyRegistry()
        registry.register(Skip())
        with self.assertRaises(StrategyError):
            registry.register(Skip())

    def test_strategy_cannot_change_session_identity(self) -> None:
        class Broken:
            strategy_id = "broken"
            priority = 1

            def propose(self, observation: Observation) -> Command:
                return Command("c", ADAPTER_ID, ACTION_CAPABILITY, "other", {"action": {"type": "rest"}})

        registry = StrategyRegistry()
        registry.register(Broken())
        with self.assertRaises(StrategyError):
            registry.select(self.observation)


class ClawRoyaleContractTests(unittest.TestCase):
    def test_action_contract_accepts_known_complete_action(self) -> None:
        self.assertEqual(validate_action({"type": "move", "regionId": "r2"}), ())

    def test_action_contract_rejects_unknown_action(self) -> None:
        self.assertEqual(validate_action({"type": "invented"}), ("unsupported_action:invented",))

    def test_package_imports_only_interoperability_and_standard_library(self) -> None:
        package = ROOT / "src" / "clawroyale_ai"
        forbidden = ("core_loop", "runtime_state", "turn_state", "game_adapters", "claw_runtime")
        for path in package.glob("*.py"):
            source = path.read_text(encoding="utf-8").lower()
            for token in forbidden:
                self.assertNotIn(token, source, f"{path.name} imports forbidden dependency {token}")

    def test_package_imports_with_only_interoperability_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            isolated = Path(directory)
            shutil.copytree(ROOT / "src" / "clawroyale_ai", isolated / "clawroyale_ai")
            shutil.copytree(
                Path(interoperability.__file__).resolve().parent,
                isolated / "interoperability",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import sys;"
                        f"sys.path.insert(0,{directory!r});"
                        "import clawroyale_ai;"
                        "assert clawroyale_ai.ADAPTER_ID=='clawroyale.ai'"
                    ),
                ],
                capture_output=True,
                check=False,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
