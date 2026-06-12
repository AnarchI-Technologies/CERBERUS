"""
Free Action Cortex.

The old filename says "abuse"; the behavior here is intentionally restrained:
take legitimate no-cooldown actions when they improve survival/combat posture.
"""

from __future__ import annotations

from combat_decider import WEAPON_BONUS, equipped_weapon
from cortex_types import CortexResult, action
from turn_state_model import TurnState


class FreeActionCortex:
    name = "free_action"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        current_name, current_bonus, _ = equipped_weapon(state)
        best_item = None
        best_bonus = current_bonus

        for item in state.inventory:
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            for weapon, (bonus, _) in WEAPON_BONUS.items():
                if weapon in label and bonus > best_bonus:
                    best_item = item
                    best_bonus = bonus

        if not best_item:
            ground_item = best_ground_weapon(state, current_bonus)
            if not ground_item:
                return []
            label = ground_item.get("typeId") or ground_item.get("name")
            return [
                CortexResult(
                    cortex=self.name,
                    intent="pickup_weapon_upgrade",
                    score=82,
                    risk=0,
                    priority=76,
                    action=action("pickup", itemId=ground_item.get("id")),
                    reason=f"free pickup weapon upgrade at feet: {label}",
                    source_facts=["F|action.free", "F|combat.weapon"],
                )
            ]

        return [
            CortexResult(
                cortex=self.name,
                intent="equip_best_weapon",
                score=35 + best_bonus - current_bonus,
                risk=0,
                priority=78,
                action=action("equip", itemId=best_item.get("id")),
                reason=f"free equip upgrade from {current_name} to {best_item.get('typeId') or best_item.get('name')}",
                source_facts=["F|action.free", "F|combat.weapon"],
            )
        ]


def weapon_bonus_for_item(item: dict) -> int:
    label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
    return max((bonus for weapon, (bonus, _range) in WEAPON_BONUS.items() if weapon in label), default=0)


def best_ground_weapon(state: TurnState, current_bonus: int) -> dict | None:
    candidates = state.visible_items + state.current_region.items
    best_item = None
    best_bonus = current_bonus
    for item in candidates:
        bonus = weapon_bonus_for_item(item)
        if item.get("id") and bonus > best_bonus:
            best_item = item
            best_bonus = bonus
    return best_item
