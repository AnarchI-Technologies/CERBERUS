"""
Threat Cortex: survival vetoes and immediate risk control.
"""

from __future__ import annotations

from clawroyale_strategies.threat import (
    THREAT_STRATEGIES,
    escape_action,
    evaluate_threat_strategies,
)
from cortex_types import CortexResult
from turn_state_model import TurnState


class ThreatCortex:
    name = "threat"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        return evaluate_threat_strategies(state, context)

    def evaluate_strategy(
        self,
        intent: str,
        state: TurnState,
        context: dict,
    ) -> tuple[CortexResult, ...]:
        try:
            strategy = THREAT_STRATEGIES[intent]
        except KeyError as error:
            raise ValueError(f"unknown threat strategy: {intent}") from error
        return strategy(state, context)


def scan(perception) -> list[CortexResult]:
    state = perception if isinstance(perception, TurnState) else TurnState.from_snapshot(perception)
    return ThreatCortex().evaluate(state, {})
