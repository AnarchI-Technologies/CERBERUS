"""Named, independently callable free-action equipment strategies."""

from __future__ import annotations

from typing import Any

from combat_decider import WEAPON_BONUS, equipped_weapon
from cortex_types import CortexResult, action
from turn_state_model import TurnState


CORTEX = "free_action"


def weapon_bonus_for_item(item: dict) -> int:
    try:
        if item.get("atkBonus") not in (None, ""):
            return max(0, int(item.get("atkBonus")))
    except (TypeError, ValueError):
        pass
    label = str(
        item.get("typeId") or item.get("type") or item.get("name") or ""
    ).lower()
    return max(
        (
            bonus
            for weapon, (bonus, _range) in WEAPON_BONUS.items()
            if weapon in label
        ),
        default=0,
    )


def armor_bonus_for_item(item: dict) -> int:
    try:
        return max(0, int(item.get("defBonus") or 0))
    except (TypeError, ValueError):
        return 0


def equipped_armor_bonus(state: TurnState) -> int:
    armor = state.self.raw.get("equippedArmor")
    return armor_bonus_for_item(armor) if isinstance(armor, dict) else 0


def best_inventory_weapon(state: TurnState) -> tuple[dict | None, int, str, int]:
    current_name, current_bonus, _ = equipped_weapon(state)
    best_item = None
    best_bonus = current_bonus
    for item in state.inventory:
        bonus = weapon_bonus_for_item(item)
        if item.get("id") and bonus > best_bonus:
            best_item = item
            best_bonus = bonus
    return best_item, best_bonus, current_name, current_bonus


def best_ground_weapon(state: TurnState, current_bonus: int) -> dict | None:
    best_item = None
    best_bonus = current_bonus
    for item in state.local_ground_items():
        bonus = weapon_bonus_for_item(item)
        if item.get("id") and bonus > best_bonus:
            best_item = item
            best_bonus = bonus
    return best_item


def equip_best_weapon(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    best_item, best_bonus, current_name, current_bonus = best_inventory_weapon(state)
    if not best_item:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="equip_best_weapon",
            score=35 + best_bonus - current_bonus,
            risk=0,
            priority=78,
            action=action("equip", itemId=best_item.get("id")),
            reason=(
                f"free equip upgrade from {current_name} to "
                f"{best_item.get('typeId') or best_item.get('name')}"
            ),
            source_facts=["F|action.free", "F|combat.weapon"],
        ),
    )


def pickup_weapon_upgrade(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    best_item, _, _, current_bonus = best_inventory_weapon(state)
    if best_item:
        return ()
    ground_item = best_ground_weapon(state, current_bonus)
    if not ground_item:
        return ()
    label = ground_item.get("typeId") or ground_item.get("name")
    return (
        CortexResult(
            cortex=CORTEX,
            intent="pickup_weapon_upgrade",
            score=82,
            risk=0,
            priority=76,
            action=action("pickup", itemId=ground_item.get("id")),
            reason=f"free pickup weapon upgrade at feet: {label}",
            source_facts=["F|action.free", "F|combat.weapon"],
        ),
    )


def equip_best_armor(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    current_armor = equipped_armor_bonus(state)
    armor = max(state.inventory, key=armor_bonus_for_item, default={})
    armor_bonus = armor_bonus_for_item(armor)
    if not armor.get("id") or armor_bonus <= current_armor:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="equip_best_armor",
            score=45 + armor_bonus - current_armor,
            risk=0,
            priority=80,
            action=action("equip", itemId=armor.get("id")),
            reason=f"free equip armor upgrade DEF +{current_armor} -> +{armor_bonus}",
            source_facts=["F|action.free", "F|combat.armor"],
        ),
    )


def pickup_armor_upgrade(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    current_armor = equipped_armor_bonus(state)
    armor = max(state.inventory, key=armor_bonus_for_item, default={})
    if armor.get("id") and armor_bonus_for_item(armor) > current_armor:
        return ()
    ground_armor = max(
        state.local_ground_items(),
        key=armor_bonus_for_item,
        default={},
    )
    ground_bonus = armor_bonus_for_item(ground_armor)
    if not ground_armor.get("id") or ground_bonus <= current_armor:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="pickup_armor_upgrade",
            score=84,
            risk=0,
            priority=77,
            action=action("pickup", itemId=ground_armor.get("id")),
            reason=f"free pickup armor upgrade at feet: DEF +{ground_bonus}",
            source_facts=["F|action.free", "F|combat.armor"],
        ),
    )


FREE_ACTION_STRATEGIES = {
    "equip_best_weapon": equip_best_weapon,
    "pickup_weapon_upgrade": pickup_weapon_upgrade,
    "equip_best_armor": equip_best_armor,
    "pickup_armor_upgrade": pickup_armor_upgrade,
}


def evaluate_free_action_strategies(
    state: TurnState, context: dict[str, Any]
) -> list[CortexResult]:
    results: list[CortexResult] = []
    for strategy in FREE_ACTION_STRATEGIES.values():
        results.extend(strategy(state, context))
    return results
