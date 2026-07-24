"""
Compatibility wrapper for isolated free-action strategies.
"""

from __future__ import annotations

from clawroyale_strategies.free_action import (
    FREE_ACTION_STRATEGIES,
    armor_bonus_for_item,
    best_ground_weapon,
    equipped_armor_bonus,
    evaluate_free_action_strategies,
    weapon_bonus_for_item,
)
from combat_decider import equipped_weapon
from cortex_types import CortexResult
from turn_state_model import TurnState


class FreeActionCortex:
    name = "free_action"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        return evaluate_free_action_strategies(state, context)

    def evaluate_strategy(
        self,
        intent: str,
        state: TurnState,
        context: dict,
    ) -> tuple[CortexResult, ...]:
        try:
            strategy = FREE_ACTION_STRATEGIES[intent]
        except KeyError as error:
            raise ValueError(f"unknown free-action strategy: {intent}") from error
        return strategy(state, context)
