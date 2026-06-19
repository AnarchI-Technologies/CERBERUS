"""
Cerberus turn entry point.

This loop keeps memory compact by default. It stores one short turn string plus
action/outcome metadata, never the full raw websocket snapshot.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_dossiers import AgentDossierStore
from autonomy_suggestions import record_autonomy_observation
from claw_contract import KNOWN_ACTION_TYPES, REQUIRED_ACTION_FIELDS, action_cost, is_cooldown_action
from combat_decider import CombatCortex
from combat_decider import target_in_attack_range
from cortex_types import rest_action
from decision_engine import make_plan as build_plan
from decision_engine import active_fallback_action
from decision_engine import has_usable_turn_facts
from ep_economy_engine import EconomyCortex
from free_action_abuse import FreeActionCortex
from knowledge_base import KnowledgeBase
from learned_policy_cortex import LearnedPolicyCortex
from longterm_memory import LongTermMemoryStore
from memory_system import CompactMemoryStore
from memory_cortex import MemoryCortex
from owner_command_cortex import OwnerCommandCortex, action_response_for_owner_command, latest_directive
from progression_cortex import ProgressionCortex
from runtime_state import owner_messages as load_owner_messages
from runtime_state import append_hellion_owner_response, last_hellion_response_for_command
from settlement_memory import remember_settlement_lessons
from social_cortex import SocialCortex
from social_runtime import enqueue_social_effects
from threat_engine import ThreatCortex
from turn_state_model import TurnState
from utility_cortex import UtilityCortex


def _call(service: Callable[..., Any] | None, fallback: Any, *args: Any, **kwargs: Any) -> Any:
    if service is None:
        return fallback
    try:
        return service(*args, **kwargs)
    except Exception as exc:
        if isinstance(fallback, dict):
            out = dict(fallback)
            out.setdefault("_warnings", []).append(
                {
                    "type": "service_error",
                    "service": getattr(service, "__name__", type(service).__name__),
                    "error": str(exc)[:240],
                }
            )
            return out
        return fallback


def normalize_action(action: Any) -> dict[str, Any]:
    if not isinstance(action, dict):
        return {"type": "rest", "reason": f"invalid action payload: {type(action).__name__}"}

    action_type = str(action.get("type") or "").strip()
    if action_type not in KNOWN_ACTION_TYPES:
        return {"type": "rest", "reason": f"unknown action type: {action_type or '<missing>'}"}

    missing = [field for field in REQUIRED_ACTION_FIELDS.get(action_type, ()) if not action.get(field)]
    if missing:
        return {
            "type": "rest",
            "reason": f"invalid {action_type} action missing {','.join(missing)}",
            "_rejected_action": {
                key: value
                for key, value in action.items()
                if key in {"type", "targetId", "targetType", "regionId", "itemId", "message", "reason"}
            },
        }
    return action


def _block(state: TurnState, action_to_reject: dict[str, Any], reason: str) -> dict[str, Any]:
    fallback = active_fallback_action(state)
    fallback["reason"] = f"blocked {reason}; {fallback.get('reason', '')}"
    fallback["_rejected_action"] = {k: action_to_reject.get(k) for k in ("type", "targetId", "targetType", "reason")}
    return fallback


def _validate_attack(action: dict[str, Any], state: TurnState) -> dict[str, Any] | None:
    target_id = str(action.get("targetId") or "")
    candidates = [*state.visible_agents, *state.visible_monsters]
    target = next((item for item in candidates if item.id == target_id and item.is_alive), None)
    if target is None:
        return _block(state, action, "invalid attack target")
    if not target_in_attack_range(state, target):
        return _block(state, action, "out-of-range attack")
    return None


def _validate_explore(action: dict[str, Any], state: TurnState) -> dict[str, Any] | None:
    terrain = state.current_region.terrain.lower()
    name = state.current_region.name.lower()
    if "ruin" not in terrain and "ruin" not in name and not state.ruins and state.connected_safe_regions():
        fallback = active_fallback_action(state)
        if fallback.get("type") == "explore":
            fallback = {"type": "move", "regionId": state.connected_safe_regions()[0]["id"], "reason": "blocked non-ruin explore; move to scout instead"}
        fallback["_rejected_action"] = {key: action.get(key) for key in ("type", "reason")}
        return fallback
    return None


def _validate_interact(action: dict[str, Any], state: TurnState) -> dict[str, Any] | None:
    if state.is_in_death_zone:
        return _block(state, action, "death-zone interact")
    return None


def _validate_use_item(action: dict[str, Any], state: TurnState) -> dict[str, Any] | None:
    if state.self.hp >= state.self.max_hp:
        item_id = str(action.get("itemId") or "")
        # Check inventory for the item to confirm it's a healing item
        item = next((i for i in state.inventory if i.get("id") == item_id), None)
        if item:
            label = str(item.get("typeId") or item.get("name") or "").lower()
            if any(term in label for term in ("medkit", "bandage")):
                return _block(state, action, "wasteful healing at full hp")
    return None


def _validate_ep_cost(action: dict[str, Any], state: TurnState) -> dict[str, Any] | None:
    action_type = str(action.get("type") or "")
    if not is_cooldown_action(action_type):
        return None
    cost = action_cost(action_type, terrain=state.current_region.terrain)
    if cost <= state.self.ep:
        return None
    fallback = rest_action("recover EP after contract cost preflight")
    fallback["reason"] = (
        f"blocked insufficient EP for {action_type}: need {cost}, have {state.self.ep}; "
        f"{fallback.get('reason', '')}"
    )
    fallback["_rejected_action"] = {
        key: action.get(key)
        for key in ("type", "targetId", "targetType", "regionId", "itemId", "reason")
        if action.get(key) is not None
    }
    fallback["_contract_cost"] = {
        "action": action_type,
        "terrain": state.current_region.terrain,
        "cost": cost,
        "available_ep": state.self.ep,
    }
    return fallback


VALIDATORS: dict[str, Callable[[dict[str, Any], TurnState], dict[str, Any] | None]] = {
    "attack": _validate_attack,
    "explore": _validate_explore,
    "interact": _validate_interact,
    "use_item": _validate_use_item,
}


def legalize_action(action: dict[str, Any], state: TurnState) -> dict[str, Any]:
    ep_validated = _validate_ep_cost(action, state)
    if ep_validated is not None:
        return ep_validated
    validator = VALIDATORS.get(str(action.get("type") or ""))
    if validator:
        validated = validator(action, state)
        return validated if validated is not None else action
    return action


def _save_or_warn(action: dict[str, Any], label: str, service: Any) -> None:
    try:
        service.save()
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": label, "error": str(exc)[:240]}
        )


def _longterm_turn_text(state: TurnState, action: dict[str, Any]) -> str:
    visible_items = len(state.visible_items) + len(state.current_region.items)
    visible_enemies = len([agent for agent in state.visible_agents if agent.is_alive])
    visible_monsters = len([monster for monster in state.visible_monsters if monster.is_alive])
    reason = str(action.get("reason") or action.get("thought") or "")[:180]
    region = state.current_region.name or state.current_region.id or "unknown"
    return (
        f"turn action={action.get('type') or 'unknown'}"
        f"; reason={reason or 'none'}"
        f"; hp={state.self.hp}/{state.self.max_hp}"
        f"; ep={state.self.ep}/{state.self.max_ep}"
        f"; region={region}"
        f"; visible_items={visible_items}"
        f"; visible_agents={visible_enemies}"
        f"; visible_monsters={visible_monsters}"
        f"; death_zone={state.is_in_death_zone}"
    )


def _remember_longterm_or_warn(
    action: dict[str, Any],
    longterm: LongTermMemoryStore | None,
    state: TurnState,
) -> None:
    if longterm is None:
        return
    try:
        key = str(action.get("type") or "unknown")
        if state.current_region.terrain:
            key += f":{state.current_region.terrain.lower()}"
        longterm.remember(
            kind="turn",
            scope="claw_royale",
            key=key,
            text=_longterm_turn_text(state, action),
            confidence=0.72,
            importance=45,
            metadata={
                "game_id": state.game_id,
                "turn": state.turn,
                "action": action.get("type"),
                "region_id": state.current_region.id,
            },
        )
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "longterm_memory", "error": str(exc)[:240]}
        )


def _remember_settlement_or_warn(action: dict[str, Any], state: dict[str, Any], memory: CompactMemoryStore) -> None:
    try:
        lessons = remember_settlement_lessons(state, memory)
        if lessons:
            action.setdefault("_side_effects", []).append(
                {"type": "settlement_lessons_recorded", "count": len(lessons)}
            )
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "settlement_memory", "error": str(exc)[:240]}
        )


def _queue_social_side_effects_or_warn(action: dict[str, Any]) -> None:
    effects = [
        effect
        for effect in action.get("_side_effects", [])
        if isinstance(effect, dict) and str(effect.get("type") or "") in {"moltybook_draft", "moltybook_follow"}
    ]
    if not effects:
        return
    try:
        queue = enqueue_social_effects(effects)
        action.setdefault("_side_effects", []).append({"type": "social_queue_updated", "queued": len(queue)})
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "social_runtime_queue", "error": str(exc)[:240]}
        )

def _event_data(event: dict[str, Any]) -> dict[str, Any]:
    return event.get("data") if isinstance(event.get("data"), dict) else event


def _event_value(data: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if value is not None and value != "":
            return str(value)
    return ""


def _event_bool(data: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
            return value.strip().lower() == "true"
    return None


def _normalized_loot_tendencies(item_name: str) -> list[str]:
    label = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(item_name))
    label = "_".join(part for part in label.split("_") if part)[:48] or "loot"
    tendencies = [f"collects_{label}"]
    if any(term in label for term in ("moltz", "smoltz")):
        tendencies.append("collects_smoltz")
    if any(term in label for term in ("relic", "pack")):
        tendencies.append("collects_high_value_loot")
        tendencies.append("collects_loadout")
    return tendencies


def _event_names_self(data: dict[str, Any], state: TurnState) -> bool:
    names = {
        str(state.self.id or "").lower(),
        str(state.self.name or "").lower(),
        "hellion",
    }
    names.discard("")
    for key in ("agentName", "victimName", "targetName", "name"):
        if str(data.get(key) or "").lower() in names:
            return True
    return False


def _remember_event_learning_or_warn(
    action: dict[str, Any],
    memory: CompactMemoryStore,
    dossiers: AgentDossierStore,
    state: TurnState,
) -> None:
    region = state.current_region.name or state.current_region.id or "unknown_region"
    try:
        self_death_recorded = False
        for event in state.events:
            event_type = str(event.get("type") or event.get("eventType") or "").lower()
            data = _event_data(event)
            killer = _event_value(data, "killerId", "attackerId", "sourceAgentId", "sourceId", "winnerId", "winnerAgentId")
            killer_name = _event_value(data, "killerName", "attackerName", "sourceName", "winnerName") or killer[:8] or "unknown"
            victim = _event_value(
                data,
                "victimId",
                "deadAgentId",
                "eliminatedAgentId",
                "defeatedAgentId",
                "agentId",
                "targetId",
            )
            victim_name = _event_value(data, "victimName", "deadAgentName", "eliminatedAgentName", "targetName", "agentName") or victim[:8] or "unknown"
            is_death_event = any(term in event_type for term in ("kill", "death", "dead", "eliminated", "defeated"))
            self_alive_flag = _event_bool(data, "selfAlive", "isAlive", "alive")
            self_dead_event = (
                is_death_event
                and state.self.id
                and (
                    victim == state.self.id
                    or _event_bool(data, "self", "isSelf", "player") is True
                    or self_alive_flag is False
                    or _event_names_self(data, state)
                )
            )
            if is_death_event and not victim and self_dead_event:
                victim = state.self.id
                victim_name = state.self.name or "Hellion"

            if is_death_event and victim:
                if killer == state.self.id and victim != state.self.id:
                    dossiers.record_kill(victim, name=victim_name)
                    dossiers.add_social_note(victim, f"lost_to_us@{region}"[:180])
                    memory.remember_lesson(
                        "combat",
                        f"success: eliminated {victim_name} in {region}; press advantage when hp and EP stay stable",
                        source=f"event:{event_type}",
                        confidence="0.82",
                    )
                elif victim == state.self.id and killer != state.self.id:
                    if killer:
                        dossiers.record_killed_us(killer, name=killer_name)
                        dossiers.add_social_note(killer, f"killed_us@{region}"[:180])
                    memory.remember_lesson(
                        "combat",
                        f"failure: {killer_name} eliminated us in {region}; respect their pressure and leave earlier",
                        source=f"event:{event_type}",
                        confidence="0.91",
                    )
                    self_death_recorded = True
                elif killer and victim and killer != victim:
                    dossiers.observe_agent(killer, name=killer_name, tendency="finishes_low_targets")
                    dossiers.observe_agent(victim, name=victim_name, tendency="dies_under_pressure")
                    dossiers.add_social_note(killer, f"beat_{victim_name}@{region}"[:180])
                    dossiers.add_social_note(victim, f"lost_to_{killer_name}@{region}"[:180])
                    memory.remember_lesson(
                        "opponents",
                        f"observed: {killer_name} eliminated {victim_name} in {region}",
                        source=f"event:{event_type}",
                        confidence="0.73",
                    )
                continue

            actor = str(data.get("agentId") or data.get("ownerId") or data.get("playerId") or "")
            item_name = str(
                data.get("itemType")
                or data.get("typeId")
                or data.get("contentType")
                or data.get("itemName")
                or "loot"
            )
            if event_type in {"relic_acquired", "pack_acquired"} and actor:
                domain = "progression"
                if actor == state.self.id:
                    memory.remember_lesson(
                        domain,
                        f"success: secured {item_name} in {region}; convert claim into safe extraction",
                        source=f"event:{event_type}",
                        confidence="0.77",
                    )
                else:
                    actor_name = str(data.get("agentName") or actor[:8])
                    for tendency in _normalized_loot_tendencies(item_name):
                        dossiers.observe_agent(actor, name=actor_name, tendency=tendency)
                    memory.remember_lesson(
                        domain,
                        f"observed: another agent secured {item_name} in {region}",
                        source=f"event:{event_type}",
                        confidence="0.68",
                    )
                continue

            if event_type in {"relic_dropped", "pack_dropped", "relic_discarded", "pack_discarded"} and actor == state.self.id:
                memory.remember_lesson(
                    "progression",
                    f"failure: lost {item_name} in {region}; do not overstay contested loot",
                    source=f"event:{event_type}",
                    confidence="0.85",
                )

        if state.self.id and not state.self.is_alive and not self_death_recorded:
            memory.remember_lesson(
                "combat",
                f"failure: Hellion death registered in {region}; preserve exits and heal before collapse",
                source="state:self_dead",
                confidence="0.88",
            )

        for effect in action.get("_side_effects", []):
            if not isinstance(effect, dict):
                continue
            if effect.get("type") == "validated_strategy_soundbite":
                agent_id = str(effect.get("agentId") or "")
                marker = str(effect.get("marker") or "")
                if agent_id and marker:
                    dossiers.add_social_note(agent_id, f"validated:{marker[:48]}")
                    memory.remember_lesson(
                        "opponents",
                        f"validated strat from {agent_id}: {marker[:120]}",
                        source="social:validated_strategy",
                        confidence=str(effect.get("confidence") or "0.7"),
                    )
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "learning", "error": str(exc)[:240]}
        )


def _respond_to_owner_command_or_warn(
    action: dict[str, Any],
    owner_directives: list[dict[str, Any]] | None,
) -> None:
    directive = latest_directive(owner_directives or [])
    if not directive:
        return
    command_id = str(directive.get("id") or "")
    if not command_id:
        return
    last_response = last_hellion_response_for_command(command_id)
    if str(last_response.get("status") or "") in {"executing", "blocked", "overridden", "heard_unmapped", "heard_context"}:
        return
    try:
        response = action_response_for_owner_command(directive, action)
        append_hellion_owner_response(
            response["text"],
            command_id=command_id,
            status=response["status"],
        )
        action.setdefault("_side_effects", []).append(
            {"type": "owner_command_response", "command_id": command_id, "status": response["status"]}
        )
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "owner_messages", "error": str(exc)[:240]}
        )


def _record_autonomy_or_warn(action: dict[str, Any], state: TurnState | dict[str, Any]) -> None:
    try:
        record_autonomy_observation(state, action)
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": "autonomy_observations", "error": str(exc)[:240]}
        )


def cerberus_tick(
    state: dict[str, Any],
    *,
    memory_store: CompactMemoryStore | None = None,
    dossier_store: AgentDossierStore | None = None,
    longterm_store: LongTermMemoryStore | None = None,
    owner_command_messages: list[dict[str, Any]] | None = None,
    observe_fn: Callable[[dict[str, Any]], Any] | None = None,
    threat_scan: Callable[[Any], Any] | None = None,
    opportunity_scan: Callable[[Any], Any] | None = None,
    make_plan: Callable[..., dict[str, Any]] | None = None,
    select_action: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Called once per turn/cooldown cycle."""

    if not isinstance(state, dict):
        state = {"view": {}, "_warning": f"non-dict snapshot ignored: {type(state).__name__}"}

    memory = memory_store or CompactMemoryStore().load()
    dossiers = dossier_store or AgentDossierStore().load()
    longterm = longterm_store
    if longterm is None and memory_store is None and dossier_store is None:
        longterm = LongTermMemoryStore()
    owner_directives = owner_command_messages
    if owner_directives is None and memory_store is None and dossier_store is None:
        owner_directives = load_owner_messages()
    knowledge = KnowledgeBase().load()
    turn_state = TurnState.from_snapshot(state)
    if not has_usable_turn_facts(turn_state):
        action = {"type": "rest", "reason": "waiting for usable live turn facts"}
        _respond_to_owner_command_or_warn(action, owner_directives)
        _record_autonomy_or_warn(action, turn_state)
        memory.remember_turn(state, action=action)
        _save_or_warn(action, "memory", memory)
        _save_or_warn(action, "dossiers", dossiers)
        _remember_longterm_or_warn(action, longterm, turn_state)
        return action
    perception = _call(observe_fn, turn_state, turn_state)
    threats = _call(threat_scan, [], perception)
    opportunities = _call(opportunity_scan, [], perception)

    planner = build_plan
    if make_plan is not None:
        planner = make_plan
    plan = _call(
        planner,
        {"action": {"type": "rest"}, "reason": "no planner wired"},
        state=turn_state,
        threats=threats,
        opportunities=opportunities,
        memory=memory.agent_context(),
        memory_store=memory,
        dossier_store=dossiers,
        owner_messages=owner_directives or [],
        knowledge=knowledge,
        cortexes=[
            ThreatCortex(),
            OwnerCommandCortex(),
            LearnedPolicyCortex(),
            FreeActionCortex(),
            UtilityCortex(),
            ProgressionCortex(),
            CombatCortex(),
            EconomyCortex(),
            SocialCortex(dossier_store=dossiers),
            MemoryCortex(memory_store=memory, dossier_store=dossiers),
        ],
    )
    action = _call(
        select_action,
        plan.get("action", {"type": "rest"}),
        plan,
        state,
    )
    action = normalize_action(action)
    action = normalize_action(legalize_action(action, turn_state))
    _respond_to_owner_command_or_warn(action, owner_directives)
    _remember_settlement_or_warn(action, state, memory)
    _remember_event_learning_or_warn(action, memory, dossiers, turn_state)
    _queue_social_side_effects_or_warn(action)
    _record_autonomy_or_warn(action, turn_state)

    memory.remember_turn(state, action=action)
    _save_or_warn(action, "memory", memory)
    _save_or_warn(action, "dossiers", dossiers)
    _remember_longterm_or_warn(action, longterm, turn_state)
    return action
