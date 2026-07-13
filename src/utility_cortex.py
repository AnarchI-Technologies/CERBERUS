"""Utility item cortex aligned to the live v1.13 inventory fields."""

from __future__ import annotations

from cortex_types import CortexResult, action
from turn_state_model import TurnState


VISION_TERMS = ("binocular",)
ENERGY_TERMS = ("energy_drink", "energy drink", "battery", "stimulant")


def _label(item: dict) -> str:
    return str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()


def _find_item(state: TurnState, terms: tuple[str, ...]) -> dict | None:
    for item in state.inventory:
        label = _label(item)
        if item.get("id") and any(term in label for term in terms):
            return item
    return None


def map_knowledge_sparse(state: TurnState) -> bool:
    return len(state.visible_regions) < 4 and len(state.connected_regions) <= 2


class UtilityCortex:
    name = "utility"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        if not state.can_take_main_action:
            return []

        results: list[CortexResult] = []
        energy = state.best_energy_item() or _find_item(state, ENERGY_TERMS)
        if energy and state.self.ep <= 1:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="restore_ep_with_energy_item",
                    score=84,
                    risk=0,
                    priority=86,
                    action=action("use_item", itemId=energy.get("id")),
                    reason="utility: EP bottleneck; use energy item before resting",
                    source_facts=["F|items.recovery", "F|action.cost"],
                )
            )

        vision = _find_item(state, VISION_TERMS)
        if vision and not state.visible_agents and not state.visible_monsters and len(state.visible_regions) < 3:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="use_vision_utility",
                    score=58,
                    risk=1,
                    priority=57,
                    action=action("use_item", itemId=vision.get("id")),
                    reason="utility: improve scouting before blind movement",
                    source_facts=["F|action.cost", "F|map.scout"],
                )
            )

        return results
