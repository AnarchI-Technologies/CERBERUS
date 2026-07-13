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
        results: list[CortexResult] = []
        best_item = None
        best_bonus = current_bonus

        for item in state.inventory:
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            for weapon, (bonus, _) in WEAPON_BONUS.items():
                if weapon in label and bonus > best_bonus:
                    best_item = item
                    best_bonus = bonus

        if best_item:
            results.append(
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
            )
        else:
            ground_item = best_ground_weapon(state, current_bonus)
            if ground_item:
                label = ground_item.get("typeId") or ground_item.get("name")
                results.append(
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
                )

        current_armor = equipped_armor_bonus(state)
        armor = max(state.inventory, key=armor_bonus_for_item, default={})
        armor_bonus = armor_bonus_for_item(armor)
        if armor.get("id") and armor_bonus > current_armor:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="equip_best_armor",
                    score=45 + armor_bonus - current_armor,
                    risk=0,
                    priority=80,
                    action=action("equip", itemId=armor.get("id")),
                    reason=f"free equip armor upgrade DEF +{current_armor} -> +{armor_bonus}",
                    source_facts=["F|action.free", "F|combat.armor"],
                )
            )
        else:
            ground_armor = max(state.local_ground_items(), key=armor_bonus_for_item, default={})
            ground_bonus = armor_bonus_for_item(ground_armor)
            if ground_armor.get("id") and ground_bonus > current_armor:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="pickup_armor_upgrade",
                        score=84,
                        risk=0,
                        priority=77,
                        action=action("pickup", itemId=ground_armor.get("id")),
                        reason=f"free pickup armor upgrade at feet: DEF +{ground_bonus}",
                        source_facts=["F|action.free", "F|combat.armor"],
                    )
                )
        return results


def weapon_bonus_for_item(item: dict) -> int:
    try:
        if item.get("atkBonus") not in (None, ""):
            return max(0, int(item.get("atkBonus")))
    except (TypeError, ValueError):
        pass
    label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
    return max((bonus for weapon, (bonus, _range) in WEAPON_BONUS.items() if weapon in label), default=0)


def armor_bonus_for_item(item: dict) -> int:
    try:
        return max(0, int(item.get("defBonus") or 0))
    except (TypeError, ValueError):
        return 0


def equipped_armor_bonus(state: TurnState) -> int:
    armor = state.self.raw.get("equippedArmor")
    return armor_bonus_for_item(armor) if isinstance(armor, dict) else 0


def best_ground_weapon(state: TurnState, current_bonus: int) -> dict | None:
    candidates = state.local_ground_items()
    best_item = None
    best_bonus = current_bonus
    for item in candidates:
        bonus = weapon_bonus_for_item(item)
        if item.get("id") and bonus > best_bonus:
            best_item = item
            best_bonus = bonus
    return best_item
