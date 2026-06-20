"""Owner command cortex.

Private owner messages are standing directives, not raw prompts. This module
parses a small deterministic command vocabulary and emits ordinary cortex
intents that still pass through action normalization and legalizers.
"""

from __future__ import annotations

from typing import Any

from combat_decider import target_in_attack_range, target_score
from cortex_types import CortexResult, action, rest_action
from external_wisdom import shared_public_line
from free_action_abuse import best_ground_weapon, equipped_weapon, weapon_bonus_for_item
from turn_state_model import TurnState


VALUE_TERMS = (
    "moltz", "smoltz", "relic", "pack", "loot", "value", "pickup", "pick up", "grab", "collect",
    "harvest", "scoop", "take item", "take items", "get paid", "money", "cash", "currency", "reward",
    "treasure", "drops", "ground loot", "valuable", "resources",
)
HEAL_TERMS = (
    "heal", "medkit", "bandage", "recover", "patch up", "use health", "stay healthy", "top off",
    "restore hp", "restore health", "low hp", "drink potion", "use potion", "health first", "survive damage",
)
WEAPON_TERMS = (
    "weapon", "equip", "sharper", "dagger", "sword", "katana", "sniper", "arm yourself", "best weapon",
    "upgrade weapon", "pick weapon", "grab weapon", "switch weapon", "equip strongest", "use stronger",
    "stop punching", "no fists", "find weapon", "weapon first", "arm up", "gear up",
)
AVOID_TERMS = (
    "avoid fight", "avoid combat", "do not fight", "don't fight", "stop fighting", "evade", "run away",
    "kite away", "disengage", "avoid enemies", "avoid agents", "avoid guardian", "avoid guardians",
)
REST_BLOCK_TERMS = (
    "do not rest", "don't rest", "stop resting", "quit resting", "no rest", "rest less", "stop idling",
    "dont rest", "do not idle", "don't idle",
)
SCOUT_TERMS = (
    "scout", "explore", "move", "rotate", "path", "map", "look around", "find route", "search",
    "roam", "navigate", "go north", "go south", "go east", "go west", "advance", "push forward",
    "find ruins", "find loot", "find enemies", "leave empty area", "stop standing still", "unstick",
)
ATTACK_TERMS = (
    "attack", "fight", "kill", "hunt", "smash", "destroy", "aggressive", "pressure", "push",
    "engage", "target", "finish", "execute", "eliminate", "hit", "strike", "shoot", "stab",
    "punish", "duel", "chase", "secure kill",
)
GUARDIAN_TERMS = ("guardian", "guardians", "monster", "monsters", "npc", "boss", "sentinel")
DEFENSIVE_TERMS = (
    "defensive", "survive", "safe", "safety", "stay alive", "preserve hp", "do not die", "risk off",
    "play safe", "safer", "retreat", "deathzone", "death zone", "storm", "danger", "dangerous",
    "protect yourself", "conserve hp", "avoid death", "live first",
)
RUIN_TERMS = (
    "ruin", "ruins", "progress", "progression", "objective", "relic objective", "open ruin",
    "finish objective", "farm relic", "relic room", "explore ruins",
)
TAUNT_TERMS = ("taunt", "talk", "say", "trash talk", "wisecrack", "mock", "chirp", "roast", "comment", "speak")
BROADCAST_TERMS = (
    "broadcast", "announce", "public thought", "spectator", "spectators", "say publicly",
    "tell the arena", "post thought", "public message",
)
PROFIT_TERMS = (
    "profit", "yield", "premium", "earn", "earning", "farm", "bankroll", "paid game", "paid games", "grind",
    "roi", "return", "daily target", "1000", "make money", "increase balance", "balance hasnt changed",
    "balance hasn't changed", "not profitable", "wasting money",
)
PERSONA_TERMS = ("persona", "tone", "sarcastic", "witty", "voice", "style", "personality", "hellion-like", "brand")
MAP_TERMS = (
    "map", "game map", "hex", "hexes", "heartbeat", "refresh map", "live state", "show map",
    "update map", "where are you", "where is hellion", "current room", "current region",
)
LEAVE_TERMS = (
    "leave room",
    "leave game",
    "exit room",
    "stale room",
    "stuck room",
    "stale game",
    "abort game",
    "abort stale",
    "abort stale game",
    "force leave",
    "forfeit",
    "surrender",
)
PAID_TERMS = ("paid ready", "paid game", "paid games", "premium", "force paid", "join paid")
FREE_TERMS = ("free game", "free games", "force free", "join free")
LOADOUT_TERMS = ("loadout", "build", "gear", "profile", "pack", "reforge", "shop", "buy", "top up", "smoltz top-up")
MEMORY_TERMS = ("memory", "learn", "lesson", "remember", "forget", "training", "evidence", "suggested edit", "self edit")
SOCIAL_TERMS = ("moltbook", "moltybook", "post", "comment", "feed", "social", "reply", "follow", "subclaw")
WALLET_TERMS = ("wallet", "balance", "deposit", "withdraw", "transfer", "fund", "funds", "private key", "owner wallet")
DEPLOY_TERMS = ("render", "deploy", "deployment", "disk", "restart", "rebuild", "version", "environment", "env var", "logs")
STREAM_TERMS = ("stream", "twitch", "chat", "viewer", "tip", "donation", "overlay", "avatar")
DIAGNOSTIC_TERMS = (
    "balance hasnt changed",
    "balance hasn't changed",
    "what is going on",
    "why",
    "stuck",
    "not earning",
    "not making",
    "runtime",
    "diagnose",
    "blocker",
    "blockers",
    "healthz",
    "readiness",
    "status",
    "what are you doing",
    "what is she doing",
    "why aren't you",
    "why arent you",
    "broken",
    "not live",
)

CONTEXT_CATEGORY_TERMS = {
    "profit": PROFIT_TERMS,
    "persona": PERSONA_TERMS,
    "map": MAP_TERMS,
    "leave_game": LEAVE_TERMS,
    "paid_mode": PAID_TERMS,
    "free_mode": FREE_TERMS,
    "loadout": LOADOUT_TERMS,
    "memory": MEMORY_TERMS,
    "social": SOCIAL_TERMS,
    "wallet": WALLET_TERMS,
    "deploy": DEPLOY_TERMS,
    "stream": STREAM_TERMS,
    "diagnostic": DIAGNOSTIC_TERMS,
}


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

        if any(term in text for term in DEFENSIVE_TERMS):
            defensive = defensive_action(state)
            if defensive:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_defensive_posture",
                        score=97,
                        risk=1,
                        priority=97,
                        veto=True,
                        action=defensive,
                        reason="owner directive: defensive survival posture",
                        source_facts=["F|owner.private_command", "F|safety.survival"],
                    )
                )

        if any(term in text for term in ATTACK_TERMS):
            target = best_attack_target(state, guardian_only=any(term in text for term in GUARDIAN_TERMS))
            if target:
                target_type = "monster" if target.kind == "monster" else "agent"
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_attack_pressure",
                        score=94,
                        risk=max(5, target.atk * 0.7),
                        priority=98,
                        veto=True,
                        action=action("attack", targetId=target.id, targetType=target_type),
                        reason=f"owner directive: attack pressure on {target_type} {target.name or target.id[:8]}",
                        source_facts=["F|owner.private_command", "F|combat.attack"],
                    )
                )

        if any(term in text for term in RUIN_TERMS):
            ruin_action = progression_action(state)
            if ruin_action:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="owner_requested_progression",
                        score=89,
                        risk=8,
                        priority=97,
                        veto=True,
                        action=ruin_action,
                        reason="owner directive: prioritize ruin/progression objective",
                        source_facts=["F|owner.private_command", "F|progression.ruin"],
                    )
                )

        if "rest" in text and not any(term in text for term in REST_BLOCK_TERMS):
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="owner_requested_rest",
                    score=90,
                    risk=0,
                    priority=95,
                    veto=True,
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
                            priority=94,
                            veto=True,
                            action=action("move", regionId=region_id),
                            reason="owner directive: scout by moving to a safe connected region",
                            source_facts=["F|owner.private_command", "F|map.scout"],
                        )
                    )
                    break

        if any(term in text for term in TAUNT_TERMS + BROADCAST_TERMS):
            message = taunt_message(directive)
            if any(term in text for term in BROADCAST_TERMS) and state.has_broadcast_channel:
                social_action = action("broadcast", message=message)
            else:
                social_action = action("talk", message=message)
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="owner_requested_public_voice",
                    score=80,
                    risk=0,
                    priority=91,
                    veto=True,
                    action=social_action,
                    reason="owner directive: speak publicly without exposing private strategy",
                    source_facts=["F|owner.private_command", "F|social.sanitized"],
                )
            )

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
    if any(term in normalized for term in DEFENSIVE_TERMS):
        categories.append("defensive")
    if any(term in normalized for term in ATTACK_TERMS):
        categories.append("attack")
    if any(term in normalized for term in GUARDIAN_TERMS):
        categories.append("guardian")
    if any(term in normalized for term in RUIN_TERMS):
        categories.append("progression")
    if "rest" in normalized and not any(term in normalized for term in REST_BLOCK_TERMS):
        categories.append("rest")
    if any(term in normalized for term in SCOUT_TERMS):
        categories.append("scout")
    if any(term in normalized for term in TAUNT_TERMS):
        categories.append("taunt")
    if any(term in normalized for term in BROADCAST_TERMS):
        categories.append("broadcast")
    for category, terms in CONTEXT_CATEGORY_TERMS.items():
        if any(term in normalized for term in terms):
            categories.append(category)
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
            "status": "heard_context",
            "text": "I heard you. That does not map to a deterministic action yet, but I will keep it as owner context instead of pretending certainty.",
        }
    context_only = [
        item
        for item in categories
        if item in CONTEXT_CATEGORY_TERMS
    ]
    executable = [item for item in categories if item not in set(context_only)]
    if context_only and not executable:
        return {
            "status": "heard_context",
            "text": f"I heard you. I agree with the direction and will treat it as standing context: {', '.join(context_only)}.",
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
            "status": "heard_context",
            "text": "I heard the command, but it did not map to a deterministic action family yet.",
        }
    context_only = set(CONTEXT_CATEGORY_TERMS)
    executable = [item for item in categories if item not in context_only]
    if not executable:
        return {
            "status": "heard_context",
            "text": f"I heard you and agree with the direction. Holding this as context: {', '.join(categories)}.",
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


def best_attack_target(state: TurnState, *, guardian_only: bool = False):
    candidates = []
    if not guardian_only:
        candidates.extend(
            agent
            for agent in state.visible_agents
            if agent.is_alive and agent.id != state.self.id and target_in_attack_range(state, agent)
        )
    candidates.extend(
        monster
        for monster in state.visible_monsters
        if monster.is_alive
        and target_in_attack_range(state, monster)
        and (not guardian_only or "guardian" in f"{monster.name} {monster.id}".lower())
    )
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: target_score(state, item), reverse=True)[0]


def defensive_action(state: TurnState) -> dict[str, Any] | None:
    heal = state.best_heal_item()
    if heal and (state.is_low_hp or state.self.hp <= int(state.self.max_hp * 0.75)):
        return action("use_item", itemId=heal.get("id"))
    for region in state.connected_safe_regions():
        region_id = region.get("id") if isinstance(region, dict) else ""
        if region_id:
            return action("move", regionId=region_id)
    if state.self.ep <= 1:
        return rest_action("owner directive: defensive recovery")
    return None


def progression_action(state: TurnState) -> dict[str, Any] | None:
    terrain = state.current_region.terrain.lower()
    name = state.current_region.name.lower()
    if state.can_take_main_action and state.self.ep > 0 and ("ruin" in terrain or "ruin" in name or state.ruins):
        return action("explore")
    for region in state.connected_safe_regions():
        label = f"{region.get('name', '')} {region.get('terrain', '')} {region.get('id', '')}".lower() if isinstance(region, dict) else str(region).lower()
        region_id = region.get("id") if isinstance(region, dict) else str(region)
        if region_id and ("ruin" in label or not state.current_region.id):
            return action("move", regionId=region_id)
    return None


def taunt_message(command: dict[str, Any]) -> str:
    raw = str(command.get("text") or command.get("message") or "")
    for marker in ("say ", "talk ", "taunt ", "broadcast ", "announce "):
        index = raw.lower().find(marker)
        if index >= 0:
            candidate = raw[index + len(marker) :].strip(" :\"'")
            if candidate:
                return sanitize_public_message(candidate)
    return shared_public_line(
        "owner_taunt",
        "Hellion heard the owner. The arena may now update its emergency plans.",
    )


def sanitize_public_message(text: str) -> str:
    blocked = ("private key", "api key", "secret", "wallet key", "mnemonic", "password", "pin")
    out = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())
    for term in blocked:
        out = out.replace(term, "[private]")
        out = out.replace(term.title(), "[private]")
    return out[:200]


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
        if "relic" in label or "pack" in label:
            score = 115
        elif "moltz" in label or "smoltz" in label:
            score = 90
        elif "medkit" in label or "bandage" in label:
            score = 65
        elif "loot" in label:
            score = 50
        if score > best_score:
            best = item
            best_score = score
    return best
