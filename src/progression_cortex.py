"""
Progression Cortex: Ruins, Relics, Packs, settlement retention.
"""

from __future__ import annotations

from cortex_types import CortexResult, action
from risk_engine import progression_value_at_risk
from turn_state_model import TurnState


class ProgressionCortex:
    name = "progression"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        region = state.current_region
        terrain = region.terrain.lower()
        region_name = region.name.lower()
        looks_like_ruin = "ruin" in terrain or "ruin" in region_name or bool(state.ruins)

        if (
            looks_like_ruin
            and state.can_take_main_action
            and state.self.ep >= 1
            and not state.alert_active
            and not state.visible_agents
            and not state.is_low_hp
        ):
            future_alert = state.alert_gauge + 2
            risk = (10 if future_alert < 10 else 45) + progression_value_at_risk(state)
            score = 72 if future_alert < 10 else 48
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="explore_ruin",
                    score=score,
                    risk=risk,
                    priority=72,
                    action=action("explore"),
                    reason=f"ruin progression available; alert {state.alert_gauge}->~{future_alert}",
                    source_facts=[
                        "F|progression.ruin",
                        "F|safety.alert",
                        "F|progression.settlement",
                    ],
                )
            )

        carried_relics = [
            item
            for item in state.inventory
            if "relic" in str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
        ]
        carried_packs = [
            item
            for item in state.inventory
            if "pack" in str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
        ]
        if carried_relics or carried_packs:
            cargo_risk = max(20 if state.self.hp < 50 else 5, progression_value_at_risk(state))
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="preserve_progression_cargo",
                    score=65 + 5 * len(carried_relics) + 12 * len(carried_packs),
                    risk=cargo_risk,
                    priority=70,
                    action=None,
                    reason=f"carrying relics={len(carried_relics)} packs={len(carried_packs)}; bias toward survival",
                    source_facts=["F|progression.settlement", "F|progression.inventory"],
                )
            )

        return results
