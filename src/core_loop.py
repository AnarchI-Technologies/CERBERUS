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
from claw_contract import KNOWN_ACTION_TYPES, REQUIRED_ACTION_FIELDS
from combat_decider import CombatCortex
from combat_decider import target_in_attack_range
from decision_engine import make_plan as build_plan
from decision_engine import active_fallback_action
from decision_engine import has_usable_turn_facts
from ep_economy_engine import EconomyCortex
from free_action_abuse import FreeActionCortex
from knowledge_base import KnowledgeBase
from longterm_memory import LongTermMemoryStore
from memory_system import CompactMemoryStore
from memory_cortex import MemoryCortex
from owner_command_cortex import OwnerCommandCortex, action_response_for_owner_command, latest_directive
from progression_cortex import ProgressionCortex
from runtime_state import owner_messages as load_owner_messages
from runtime_state import append_hellion_owner_response, last_hellion_response_for_command
from social_cortex import SocialCortex
from threat_engine import ThreatCortex
from turn_state_model import TurnState


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


def legalize_action(action: dict[str, Any], state: TurnState) -> dict[str, Any]:
    action_type = str(action.get("type") or "")
    if action_type == "attack":
        target_id = str(action.get("targetId") or "")
        candidates = [*state.visible_agents, *state.visible_monsters]
        target = next((item for item in candidates if item.id == target_id and item.is_alive), None)
        if target is None:
            fallback = active_fallback_action(state)
            fallback["reason"] = "blocked invalid attack target; " + str(fallback.get("reason") or "")
            fallback["_rejected_action"] = {key: action.get(key) for key in ("type", "targetId", "targetType", "reason")}
            return fallback
        if not target_in_attack_range(state, target):
            fallback = active_fallback_action(state)
            fallback["reason"] = "blocked out-of-range attack; " + str(fallback.get("reason") or "")
            fallback["_rejected_action"] = {key: action.get(key) for key in ("type", "targetId", "targetType", "reason")}
            return fallback
    if action_type == "explore":
        terrain = state.current_region.terrain.lower()
        name = state.current_region.name.lower()
        if "ruin" not in terrain and "ruin" not in name and not state.ruins and state.connected_safe_regions():
            fallback = active_fallback_action(state)
            if fallback.get("type") == "explore":
                fallback = {"type": "move", "regionId": state.connected_safe_regions()[0]["id"], "reason": "blocked non-ruin explore; move to scout instead"}
            fallback["_rejected_action"] = {key: action.get(key) for key in ("type", "reason")}
            return fallback
    if action_type == "interact" and state.is_in_death_zone:
        fallback = active_fallback_action(state)
        fallback["reason"] = "blocked death-zone interact; " + str(fallback.get("reason") or "")
        fallback["_rejected_action"] = {key: action.get(key) for key in ("type", "targetId", "reason")}
        return fallback
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
        owner_messages=owner_directives or [],
        knowledge=knowledge,
        cortexes=[
            ThreatCortex(),
            OwnerCommandCortex(),
            FreeActionCortex(),
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

    memory.remember_turn(state, action=action)
    _save_or_warn(action, "memory", memory)
    _save_or_warn(action, "dossiers", dossiers)
    _remember_longterm_or_warn(action, longterm, turn_state)
    return action
