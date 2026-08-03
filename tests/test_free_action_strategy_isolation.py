from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from clawroyale_strategies.free_action import FREE_ACTION_STRATEGIES
from compatibility import LegacyStrategyRegistry
from free_action_abuse import FreeActionCortex
from turn_state_model import TurnState


def state(*, inventory=None, items=None):
    return TurnState.from_snapshot(
        {
            "canAct": True,
            "view": {
                "self": {
                    "id": "hellion",
                    "hp": 80,
                    "ep": 6,
                    "regionId": "r1",
                    "inventory": inventory or [],
                },
                "currentRegion": {
                    "id": "r1",
                    "items": items or [],
                },
            },
        }
    )


class FreeActionStrategyIsolationTests(unittest.TestCase):
    def test_all_four_strategies_are_named(self):
        self.assertEqual(
            tuple(FREE_ACTION_STRATEGIES),
            (
                "equip_best_weapon",
                "pickup_weapon_upgrade",
                "equip_best_armor",
                "pickup_armor_upgrade",
            ),
        )

    def test_direct_call_bypasses_whole_provider(self):
        provider = FreeActionCortex()
        registry = LegacyStrategyRegistry({"free_action_abuse": provider})
        current = state(inventory=[{"id": "sword-1", "typeId": "sword"}])
        with mock.patch.object(
            provider,
            "evaluate",
            side_effect=AssertionError("whole provider must not run"),
        ):
            results = registry.call(
                "clawroyale.equip_best_weapon",
                current,
                {},
            )
        self.assertEqual(tuple(result.intent for result in results), ("equip_best_weapon",))

    def test_weapon_and_armor_branches_remain_independent(self):
        current = state(
            inventory=[
                {"id": "sword-1", "typeId": "sword"},
                {"id": "armor-1", "typeId": "armor", "defBonus": 8},
            ]
        )
        weapon = FREE_ACTION_STRATEGIES["equip_best_weapon"](current, {})
        armor = FREE_ACTION_STRATEGIES["equip_best_armor"](current, {})
        self.assertEqual({result.intent for result in weapon}, {"equip_best_weapon"})
        self.assertEqual({result.intent for result in armor}, {"equip_best_armor"})


if __name__ == "__main__":
    unittest.main()
