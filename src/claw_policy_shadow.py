"""Observation-only v2 policy evaluation for selected Claw gameplay actions."""

from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

from claw_v2_workflow import build_free_action_contracts
from policy_engine import evaluate_action_request
from runtime_state import append_policy_shadow
from turn_state_model import TurnState
from v2_contracts import ActionRequest, Decision, Event, PolicyContext, PolicyDecision, PolicyOutcome, contract_dict


ENFORCED_FREE_ACTIONS = frozenset({"pickup", "equip", "broadcast"})


def shadow_enabled() -> bool:
    return os.getenv("CERBERUS_V2_POLICY_SHADOW_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _target(action: dict) -> str:
    return str(action.get("targetId") or action.get("regionId") or action.get("itemId") or "")


def _visible_targets(state: TurnState) -> frozenset[str]:
    values = {state.current_region.id}
    values.update(
        str(item if isinstance(item, str) else item.get("id") or "")
        for item in state.connected_regions
        if isinstance(item, (str, dict))
    )
    values.update(agent.id for agent in (*state.visible_agents, *state.visible_monsters))
    values.update(str(item.get("id") or "") for item in state.local_ground_items())
    values.update(str(item.get("id") or "") for item in state.inventory)
    return frozenset(value for value in values if value)


def _evaluate_claw_action_contracts(
    state: TurnState, action: dict, *, enforced: bool
) -> tuple[ActionRequest, PolicyDecision, dict]:
    action_type = str(action.get("type") or "rest")
    correlation_id = str(state.game_id or f"turn-{state.turn}")
    request = ActionRequest(
        request_id=str(uuid4()),
        decision_id=str(uuid4()),
        correlation_id=correlation_id,
        actor_id=str(state.self.id or state.agent_id or "unknown"),
        capability="game.action.execute",
        provider="claw_royale",
        operation=action_type,
        target=_target(action),
        consequential=action_type not in {"rest", "talk", "whisper", "broadcast"},
        origin="deterministic",
        idempotency_key=hashlib.sha256(
            json.dumps(
                {
                    "game": correlation_id,
                    "turn": state.turn,
                    "action": {key: value for key, value in action.items() if not str(key).startswith("_") and key != "reason"},
                },
                sort_keys=True,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    )
    context = PolicyContext(
        policy_id="claw-free-action-v1" if enforced else "claw-shadow-v1",
        capabilities=frozenset({"game.action.execute"}),
        allowed_targets=_visible_targets(state),
        suspended=os.getenv("CERBERUS_EMERGENCY_SUSPEND", "false").strip().lower() in {"1", "true", "yes", "on"},
        state_fresh=state.can_take_main_action or action_type in {"rest", "pickup", "equip", "talk", "whisper", "broadcast"},
        maximum_financial_amount=0.0,
        review_financial_threshold=0.0,
    )
    policy = evaluate_action_request(request, context)
    record = {
        "request": contract_dict(request),
        "policy": contract_dict(policy),
        "selected_action": {key: value for key, value in action.items() if not str(key).startswith("_")},
        "enforced": enforced,
    }
    append_policy_shadow(record)
    return request, policy, record


def evaluate_claw_action_shadow(state: TurnState, action: dict) -> dict:
    """Record policy evidence without altering or executing the selected action."""
    return _evaluate_claw_action_contracts(state, action, enforced=False)[2]


def authorize_broadcast(state: TurnState, action: dict) -> tuple[bool, dict]:
    """Enforce the first v2 seam for the non-financial broadcast action only."""
    if str(action.get("type") or "") != "broadcast":
        raise ValueError("authorize_broadcast only accepts broadcast actions")
    _request, _policy, record = _evaluate_claw_action_contracts(state, action, enforced=True)
    return record["policy"]["outcome"] == PolicyOutcome.ALLOW.value, record


def authorize_broadcast_execution(
    state: TurnState, action: dict
) -> tuple[bool, ActionRequest, PolicyDecision, dict]:
    if str(action.get("type") or "") != "broadcast":
        raise ValueError("authorize_broadcast_execution only accepts broadcast actions")
    request, policy, record = _evaluate_claw_action_contracts(state, action, enforced=True)
    return policy.outcome is PolicyOutcome.ALLOW, request, policy, record


def authorize_free_action_execution(
    state: TurnState, action: dict
) -> tuple[bool, Event, Decision, ActionRequest, PolicyDecision, dict]:
    action_type = str(action.get("type") or "")
    if action_type not in ENFORCED_FREE_ACTIONS:
        raise ValueError(f"unsupported coordinated free action: {action_type}")
    event, decision, request = build_free_action_contracts(state, action)
    context = PolicyContext(
        policy_id="claw-free-action-v1",
        capabilities=frozenset({"game.action.execute"}),
        allowed_targets=_visible_targets(state),
        suspended=os.getenv("CERBERUS_EMERGENCY_SUSPEND", "false").strip().lower() in {"1", "true", "yes", "on"},
        state_fresh=state.can_take_main_action or action_type in ENFORCED_FREE_ACTIONS,
        maximum_financial_amount=0.0,
        review_financial_threshold=0.0,
    )
    policy = evaluate_action_request(request, context)
    record = {
        "event": contract_dict(event),
        "decision": contract_dict(decision),
        "request": contract_dict(request),
        "policy": contract_dict(policy),
        "selected_action": {key: value for key, value in action.items() if not str(key).startswith("_")},
        "enforced": True,
    }
    append_policy_shadow(record)
    return policy.outcome is PolicyOutcome.ALLOW, event, decision, request, policy, record
