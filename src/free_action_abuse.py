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
            return []

        return [
            CortexResult(
                cortex=self.name,
                intent="equip_best_weapon",
                score=35 + best_bonus - current_bonus,
                risk=0,
                priority=57,
                action=action("equip", itemId=best_item.get("id")),
                reason=f"free equip upgrade from {current_name} to {best_item.get('typeId') or best_item.get('name')}",
                source_facts=["F|action.free", "F|combat.weapon"],
            )
        ]
