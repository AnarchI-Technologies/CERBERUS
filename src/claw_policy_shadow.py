"""Observation-only v2 policy evaluation for selected Claw gameplay actions."""

from __future__ import annotations

import os
from uuid import uuid4

from policy_engine import evaluate_action_request
from runtime_state import append_policy_shadow
from turn_state_model import TurnState
from v2_contracts import ActionRequest, PolicyContext, PolicyOutcome, contract_dict


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


def _evaluate_claw_action(state: TurnState, action: dict, *, enforced: bool) -> dict:
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
        idempotency_key=str(uuid4()),
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
    return record


def evaluate_claw_action_shadow(state: TurnState, action: dict) -> dict:
    """Record policy evidence without altering or executing the selected action."""
    return _evaluate_claw_action(state, action, enforced=False)


def authorize_broadcast(state: TurnState, action: dict) -> tuple[bool, dict]:
    """Enforce the first v2 seam for the non-financial broadcast action only."""
    if str(action.get("type") or "") != "broadcast":
        raise ValueError("authorize_broadcast only accepts broadcast actions")
    record = _evaluate_claw_action(state, action, enforced=True)
    return record["policy"]["outcome"] == PolicyOutcome.ALLOW.value, record
