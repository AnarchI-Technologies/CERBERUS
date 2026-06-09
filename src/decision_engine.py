"""
Multicortex arbiter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cortex_types import Cortex, CortexResult, rest_action
from knowledge_base import KnowledgeBase
from memory_system import CompactMemoryStore
from turn_state_model import TurnState


@dataclass(slots=True)
class ArbiterDecision:
    action: dict[str, Any]
    reason: str
    winner: CortexResult | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)

    def to_action(self) -> dict[str, Any]:
        out = dict(self.action)
        if self.reason and "reason" not in out:
            out["reason"] = self.reason[:700]
        if self.side_effects:
            out["_side_effects"] = self.side_effects
        return out


class Arbiter:
    def choose(self, results: list[CortexResult], state: TurnState) -> ArbiterDecision:
        side_effects = [
            effect
            for result in results
            for effect in result.side_effects
        ]
        candidates = [result for result in results if result.action]
        vetoes = [result for result in candidates if result.veto]
        pool = vetoes or candidates

        if not pool:
            return ArbiterDecision(
                action=rest_action("no cortex produced an action"),
                reason="no cortex produced an action",
                candidates=[result.to_plan_entry() for result in results],
                side_effects=side_effects,
            )

        winner = sorted(
            pool,
            key=lambda result: (result.priority, result.utility, result.score),
            reverse=True,
        )[0]
        return ArbiterDecision(
            action=winner.action or rest_action(),
            reason=winner.reason or winner.intent,
            winner=winner,
            candidates=[result.to_plan_entry() for result in results],
            side_effects=side_effects,
        )


def make_plan(
    *,
    state: dict[str, Any] | TurnState,
    threats: Any = None,
    opportunities: Any = None,
    memory: str = "",
    memory_store: CompactMemoryStore | None = None,
    knowledge: KnowledgeBase | None = None,
    cortexes: list[Cortex] | None = None,
) -> dict[str, Any]:
    turn_state = state if isinstance(state, TurnState) else TurnState.from_snapshot(state)
    kb = knowledge or KnowledgeBase().load()
    store = memory_store or CompactMemoryStore().load()
    context = {
        "threats": threats or [],
        "opportunities": opportunities or [],
        "memory": memory or store.agent_context(),
        "memory_store": store,
        "knowledge": kb,
    }
    results: list[CortexResult] = []
    for cortex in cortexes or []:
        results.extend(cortex.evaluate(turn_state, context))
    decision = Arbiter().choose(results, turn_state)
    return {
        "action": decision.to_action(),
        "reason": decision.reason,
        "winner": decision.winner.to_plan_entry() if decision.winner else None,
        "candidates": decision.candidates,
        "side_effects": decision.side_effects,
    }
