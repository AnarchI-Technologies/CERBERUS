"""
Economy Cortex: free-action value, sMoltz/shop/reforge posture, EP thrift.
"""

from __future__ import annotations

from cortex_types import CortexResult, action
from free_action_abuse import weapon_bonus_for_item
from turn_state_model import TurnState


VALUABLE_ITEM_TERMS = (
    "relic",
    "pack",
    "moltz",
    "smoltz",
    "medkit",
    "bandage",
)


class EconomyCortex:
    name = "economy"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        if state.visible_agents:
            return results

        for item in state.visible_items + state.current_region.items:
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            if weapon_bonus_for_item(item) > 0:
                continue
            if any(term in label for term in VALUABLE_ITEM_TERMS):
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="collect_free_value",
                        score=48 if "relic" not in label and "pack" not in label else 70,
                        risk=3,
                        priority=58,
                        action=action("pickup", itemId=item.get("id")),
                        reason=f"free pickup value: {label}",
                        source_facts=["F|action.free", "F|economy.free", "F|economy.reforge"],
                    )
                )
                break

        return results
