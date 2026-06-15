"""Owner command cortex.

Private owner messages are standing directives, not raw prompts. This module
parses a small deterministic command vocabulary and emits ordinary cortex
intents that still pass through action normalization and legalizers.
"""

from __future__ import annotations

from typing import Any

from cortex_types import CortexResult, action, rest_action
from free_action_abuse import best_ground_weapon, equipped_weapon, weapon_bonus_for_item
from turn_state_model import TurnState


VALUE_TERMS = ("moltz", "smoltz", "relic", "pack", "loot", "value", "pickup", "pick up")


class OwnerCommandCortex:
    name = "owner_command"

    def evaluate(self, state: TurnState, context: dict[str, Any]) -> list[CortexResult]:
        directive = latest_directive(context.get("owner_messages", []))
        if not directive:
            return []

        text = directive_text(directive)
        results: list[CortexResult] = []
        if any(term in text for term in ("heal", "medkit", "bandage")):
            heal = state.best_heal_item()
            if heal:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_heal",
                        score=96,
                        risk=0,
                        priority=96,
                        veto=True,
                        action=action("use_item", itemId=heal.get("id")),
                        reason="owner directive: heal when a recovery item is available",
                        source_facts=["F|owner.private_command", "F|items.recovery"],
                    )
                )

        if any(term in text for term in ("weapon", "equip", "sharper", "dagger", "sword", "katana", "sniper")):
            weapon_action = best_weapon_action(state)
            if weapon_action:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_weapon_posture",
                        score=90,
                        risk=0,
                        priority=91,
                        action=weapon_action,
                        reason="owner directive: improve weapon posture before spending combat turns",
                        source_facts=["F|owner.private_command", "F|combat.weapon"],
                    )
                )

        if any(term in text for term in VALUE_TERMS):
            item = best_value_item(state)
            if item:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_value_pickup",
                        score=88,
                        risk=2,
                        priority=88,
                        action=action("pickup", itemId=item.get("id")),
                        reason="owner directive: prioritize visible value",
                        source_facts=["F|owner.private_command", "F|economy.free"],
                    )
                )

        if any(term in text for term in ("avoid fight", "avoid combat", "do not fight", "don't fight", "stop fighting")):
            for region in state.connected_safe_regions():
                region_id = region.get("id") if isinstance(region, dict) else ""
                if region_id:
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="owner_requested_combat_avoidance",
                            score=84,
                            risk=3,
                            priority=94,
                            veto=True,
                            action=action("move", regionId=region_id),
                            reason="owner directive: avoid combat and reposition",
                            source_facts=["F|owner.private_command", "F|safety.movement"],
                        )
                    )
                    break

        if "rest" in text and not any(term in text for term in ("do not rest", "don't rest", "stop resting")):
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="owner_requested_rest",
                    score=55,
                    risk=0,
                    priority=82,
                    action=rest_action("owner directive: rest"),
                    reason="owner directive: rest",
                    source_facts=["F|owner.private_command"],
                )
            )

        if any(term in text for term in ("scout", "explore", "move")) and state.can_take_main_action and state.self.ep > 0:
            for region in state.connected_safe_regions():
                region_id = region.get("id") if isinstance(region, dict) else ""
                if region_id:
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="owner_requested_scouting",
                            score=62,
                            risk=5,
                            priority=74,
                            action=action("move", regionId=region_id),
                            reason="owner directive: scout by moving to a safe connected region",
                            source_facts=["F|owner.private_command", "F|map.scout"],
                        )
                    )
                    break

        if results:
            for result in results:
                result.side_effects.append({"type": "owner_command_considered", "kind": directive.get("kind", "")})
        return results


def latest_directive(messages: Any) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if isinstance(item, dict) and directive_text(item):
            return item
    return None


def directive_text(message: dict[str, Any]) -> str:
    return str(message.get("text") or message.get("message") or "").strip().lower()


def best_weapon_action(state: TurnState) -> dict[str, Any] | None:
    _current_name, current_bonus, _range = equipped_weapon(state)
    best_inventory = None
    best_inventory_bonus = current_bonus
    for item in state.inventory:
        bonus = weapon_bonus_for_item(item)
        if item.get("id") and bonus > best_inventory_bonus:
            best_inventory = item
            best_inventory_bonus = bonus
    if best_inventory:
        return action("equip", itemId=best_inventory.get("id"))

    ground = best_ground_weapon(state, current_bonus)
    if ground:
        return action("pickup", itemId=ground.get("id"))
    return None


def best_value_item(state: TurnState) -> dict[str, Any] | None:
    best = None
    best_score = 0
    for item in state.visible_items + state.current_region.items:
        label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
        if not item.get("id"):
            continue
        score = 0
        if "moltz" in label or "smoltz" in label:
            score = 90
        elif "relic" in label or "pack" in label:
            score = 80
        elif "medkit" in label or "bandage" in label:
            score = 65
        elif "loot" in label:
            score = 50
        if score > best_score:
            best = item
            best_score = score
    return best
