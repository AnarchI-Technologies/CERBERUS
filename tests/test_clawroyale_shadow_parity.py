from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

import interoperability
from clawroyale_ai import ClawRoyaleAdapter
from compatibility import ClawRoyaleShadowBridge


DEPENDENCY_ROOT = Path(interoperability.__file__).resolve().parents[2]
FIXTURE = json.loads(
    (ROOT / "tests" / "fixtures" / "claw_agent_view_sanitized.json").read_text(
        encoding="utf-8"
    )
)


class ClawRoyaleShadowParityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = ClawRoyaleShadowBridge()

    def test_sanitized_observation_has_full_parity(self) -> None:
        report = self.bridge.compare_observation(FIXTURE)
        self.assertTrue(report.passed, report.mismatches)
        self.assertEqual(len(report.checks), 6)

    def test_cooldown_closes_portable_action_window(self) -> None:
        payload = copy.deepcopy(FIXTURE)
        payload["cooldownRemainingMs"] = 250
        report = self.bridge.compare_observation(payload)
        self.assertTrue(report.passed, report.mismatches)
        can_act = next(check for check in report.checks if check.name == "can_act")
        self.assertFalse(can_act.portable)

    def test_valid_action_has_parity(self) -> None:
        report = self.bridge.compare_action(
            FIXTURE,
            {"type": "attack", "targetId": "fixture-rival"},
        )
        self.assertTrue(report.passed)

    def test_invalid_action_has_parity(self) -> None:
        report = self.bridge.compare_action(FIXTURE, {"type": "attack"})
        self.assertTrue(report.passed)
        self.assertEqual(report.checks[0].portable, ("missing:targetId",))

    def test_unknown_action_has_parity(self) -> None:
        report = self.bridge.compare_action(FIXTURE, {"type": "invented"})
        self.assertTrue(report.passed)

    def test_comparison_does_not_mutate_payload(self) -> None:
        payload = copy.deepcopy(FIXTURE)
        before = json.dumps(payload, sort_keys=True)
        self.bridge.compare_observation(payload)
        self.bridge.compare_action(payload, {"type": "rest"})
        self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_report_is_serializable_evidence(self) -> None:
        report = self.bridge.compare_observation(FIXTURE)
        serialized = json.dumps(asdict(report), sort_keys=True)
        self.assertIn('"name": "match_id"', serialized)

    def test_shadow_bridge_does_not_load_live_execution_modules(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                (
                    "import sys;"
                    f"sys.path[:0]=[{str(ROOT / 'src')!r},"
                    f"{str(DEPENDENCY_ROOT / 'src')!r},"
                    f"{str(DEPENDENCY_ROOT / 'data')!r}];"
                    "import compatibility;"
                    "assert 'claw_runtime' not in sys.modules;"
                    "assert 'core_loop' not in sys.modules;"
                    "assert 'execution_coordinator' not in sys.modules"
                ),
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_portable_observation_records_cooldown_evidence(self) -> None:
        async def transport(_: dict) -> dict:
            raise AssertionError("normalization must not call transport")

        payload = copy.deepcopy(FIXTURE)
        payload["cooldownRemainingMs"] = 300
        observation = ClawRoyaleAdapter(transport).normalize_observation(
            payload,
            session_id="s",
            sequence=1,
            observation_id="o",
        )
        self.assertEqual(observation.payload["cooldown_remaining_ms"], 300)
        self.assertTrue(observation.payload["can_act_advertised"])
        self.assertFalse(observation.payload["can_act"])


if __name__ == "__main__":
    unittest.main()
