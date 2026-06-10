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
from decision_engine import make_plan as build_plan
from ep_economy_engine import EconomyCortex
from free_action_abuse import FreeActionCortex
from knowledge_base import KnowledgeBase
from memory_system import CompactMemoryStore
from memory_cortex import MemoryCortex
from progression_cortex import ProgressionCortex
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


def _save_or_warn(action: dict[str, Any], label: str, service: Any) -> None:
    try:
        service.save()
    except Exception as exc:
        action.setdefault("_warnings", []).append(
            {"type": "save_error", "store": label, "error": str(exc)[:240]}
        )


def cerberus_tick(
    state: dict[str, Any],
    *,
    memory_store: CompactMemoryStore | None = None,
    dossier_store: AgentDossierStore | None = None,
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
    knowledge = KnowledgeBase().load()
    turn_state = TurnState.from_snapshot(state)
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
        knowledge=knowledge,
        cortexes=[
            ThreatCortex(),
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

    memory.remember_turn(state, action=action)
    _save_or_warn(action, "memory", memory)
    _save_or_warn(action, "dossiers", dossiers)
    return action
