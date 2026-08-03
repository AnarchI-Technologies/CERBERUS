from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from clawroyale_strategies.threat import THREAT_STRATEGIES
from compatibility import LegacyStrategyRegistry
from threat_engine import ThreatCortex
from turn_state_model import TurnState


def state(**self_overrides):
    actor = {
        "id": "hellion",
        "hp": 75,
        "maxHp": 100,
        "ep": 8,
        "maxEp": 10,
        "regionId": "r1",
        "inventory": [],
    }
    actor.update(self_overrides)
    return TurnState.from_snapshot(
        {
            "canAct": True,
            "view": {
                "self": actor,
                "currentRegion": {
                    "id": "r1",
                    "connections": [{"id": "r2", "terrain": "plains"}],
                    "items": [],
                },
            },
        }
    )


class ThreatStrategyIsolationTests(unittest.TestCase):
    def test_all_six_threat_strategies_are_individually_named(self):
        self.assertEqual(
            tuple(THREAT_STRATEGIES),
            (
                "escape_death_zone",
                "emergency_death_zone_probe",
                "heal_low_hp",
                "break_guardian_alert_exposure",
                "cool_alert",
                "recover_ep_floor",
            ),
        )

    def test_direct_registry_call_does_not_evaluate_whole_provider(self):
        provider = ThreatCortex()
        registry = LegacyStrategyRegistry({"threat_engine": provider})
        with mock.patch.object(
            provider,
            "evaluate",
            side_effect=AssertionError("whole provider must not run"),
        ):
            result = registry.call(
                "clawroyale.recover_ep_floor",
                state(ep=1),
                {},
            )
        self.assertEqual(tuple(item.intent for item in result), ("recover_ep_floor",))

    def test_each_callable_emits_only_its_own_intent(self):
        scenarios = {
            "heal_low_hp": state(
                hp=10,
                inventory=[{"id": "med-1", "typeId": "medkit"}],
            ),
            "recover_ep_floor": state(ep=1),
        }
        for intent, current in scenarios.items():
            with self.subTest(intent=intent):
                results = THREAT_STRATEGIES[intent](current, {})
                self.assertTrue(results)
                self.assertEqual({result.intent for result in results}, {intent})

    def test_compatibility_wrapper_preserves_strategy_order(self):
        current = state(
            hp=10,
            ep=4,
            inventory=[{"id": "med-1", "typeId": "medkit"}],
        )
        intents = tuple(result.intent for result in ThreatCortex().evaluate(current, {}))
        self.assertEqual(intents, ("heal_low_hp",))


if __name__ == "__main__":
    unittest.main()
