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
HEAL_TERMS = ("heal", "medkit", "bandage")
WEAPON_TERMS = ("weapon", "equip", "sharper", "dagger", "sword", "katana", "sniper")
AVOID_TERMS = ("avoid fight", "avoid combat", "do not fight", "don't fight", "stop fighting")
REST_BLOCK_TERMS = ("do not rest", "don't rest", "stop resting")
SCOUT_TERMS = ("scout", "explore", "move")


class OwnerCommandCortex:
    name = "owner_command"

    def evaluate(self, state: TurnState, context: dict[str, Any]) -> list[CortexResult]:
        directive = latest_directive(context.get("owner_messages", []))
        if not directive:
            return []

        text = directive_text(directive)
        results: list[CortexResult] = []
        if any(term in text for term in HEAL_TERMS):
            heal = state.best_heal_item()
            if heal:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_heal",
                        score=99,
                        risk=0,
                        priority=99,
                        veto=True,
                        action=action("use_item", itemId=heal.get("id")),
                        reason="owner directive: heal when a recovery item is available",
                        source_facts=["F|owner.private_command", "F|items.recovery"],
                    )
                )

        if any(term in text for term in WEAPON_TERMS):
            weapon_action = best_weapon_action(state)
            if weapon_action:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_weapon_posture",
                        score=97,
                        risk=0,
                        priority=97,
                        veto=True,
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
                        score=96,
                        risk=2,
                        priority=96,
                        veto=True,
                        action=action("pickup", itemId=item.get("id")),
                        reason="owner directive: prioritize visible value",
                        source_facts=["F|owner.private_command", "F|economy.free"],
                    )
                )

        if any(term in text for term in AVOID_TERMS):
            for region in state.connected_safe_regions():
                region_id = region.get("id") if isinstance(region, dict) else ""
                if region_id:
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="owner_requested_combat_avoidance",
                            score=98,
                            risk=3,
                            priority=98,
                            veto=True,
                            action=action("move", regionId=region_id),
                            reason="owner directive: avoid combat and reposition",
                            source_facts=["F|owner.private_command", "F|safety.movement"],
                        )
                    )
                    break

        if "rest" in text and not any(term in text for term in REST_BLOCK_TERMS):
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="owner_requested_rest",
                    score=90,
                    risk=0,
                    priority=90,
                    action=rest_action("owner directive: rest"),
                    reason="owner directive: rest",
                    source_facts=["F|owner.private_command"],
                )
            )

        if any(term in text for term in SCOUT_TERMS) and state.can_take_main_action and state.self.ep > 0:
            for region in state.connected_safe_regions():
                region_id = region.get("id") if isinstance(region, dict) else ""
                if region_id:
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="owner_requested_scouting",
                            score=88,
                            risk=5,
                            priority=88,
                            action=action("move", regionId=region_id),
                            reason="owner directive: scout by moving to a safe connected region",
                            source_facts=["F|owner.private_command", "F|map.scout"],
                        )
                    )
                    break

        if results:
            for result in results:
                result.side_effects.append(
                    {
                        "type": "owner_command_considered",
                        "kind": directive.get("kind", ""),
                        "command_id": directive.get("id", ""),
                    }
                )
        return results


def is_owner_directive(message: dict[str, Any]) -> bool:
    kind = str(message.get("kind") or "").lower()
    author = str(message.get("author") or "").lower()
    if author == "hellion" or kind in {"hellion_response", "owner_ack", "owner_status"}:
        return False
    return kind in {"owner_command", "owner_message", "command"} or not kind


def latest_directive(messages: Any) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for item in reversed(messages):
        if isinstance(item, dict) and is_owner_directive(item) and directive_text(item):
            return item
    return None


def directive_text(message: dict[str, Any]) -> str:
    return str(message.get("text") or message.get("message") or "").strip().lower()


def command_categories(text: str) -> list[str]:
    normalized = str(text or "").lower()
    categories = []
    if any(term in normalized for term in HEAL_TERMS):
        categories.append("heal")
    if any(term in normalized for term in WEAPON_TERMS):
        categories.append("weapon")
    if any(term in normalized for term in VALUE_TERMS):
        categories.append("value")
    if any(term in normalized for term in AVOID_TERMS):
        categories.append("avoid_combat")
    if "rest" in normalized and not any(term in normalized for term in REST_BLOCK_TERMS):
        categories.append("rest")
    if any(term in normalized for term in SCOUT_TERMS):
        categories.append("scout")
    return categories


def acknowledge_owner_command(message: dict[str, Any]) -> dict[str, str]:
    text = directive_text(message)
    categories = command_categories(text)
    if not text:
        return {
            "status": "blocked",
            "text": "I heard static, not a command. Give me a clear directive and I will route it.",
        }
    if not categories:
        return {
            "status": "heard_unmapped",
            "text": "I heard you. I do not have a deterministic handler for that command yet, so I will keep it as context instead of pretending certainty.",
        }
    label = ", ".join(categories)
    return {
        "status": "agreed",
        "text": f"I heard you. I agree with the priority and will route it as high-priority owner intent: {label}. Survival and legal action checks still outrank everything.",
    }


def action_response_for_owner_command(command: dict[str, Any], action_payload: dict[str, Any]) -> dict[str, str]:
    categories = command_categories(directive_text(command))
    action_type = str(action_payload.get("type") or "unknown")
    if not categories:
        return {
            "status": "heard_unmapped",
            "text": "I heard the command, but it did not map to a deterministic action family yet.",
        }
    if action_payload.get("_rejected_action"):
        return {
            "status": "blocked",
            "text": f"I heard you, but the requested action was blocked by runtime legality. I chose {action_type} instead.",
        }
    if "owner directive" in str(action_payload.get("reason") or "").lower():
        return {
            "status": "executing",
            "text": f"I heard you and I agree. Executing owner-priority action: {action_type}.",
        }
    return {
        "status": "overridden",
        "text": f"I heard you. I agree with the command, but a higher-priority safety or legality rule chose {action_type} this tick.",
    }


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
