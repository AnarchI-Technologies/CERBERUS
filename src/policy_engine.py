"""Deterministic capability and authority evaluation for v2 action requests."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from v2_contracts import ActionRequest, PolicyContext, PolicyDecision, PolicyOutcome, validate_action_request


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def evaluate_action_request(request: ActionRequest, context: PolicyContext) -> PolicyDecision:
    reasons: list[str] = list(validate_action_request(request))
    outcome = PolicyOutcome.ALLOW

    if reasons:
        outcome = PolicyOutcome.DENY
    elif context.suspended:
        outcome, reasons = PolicyOutcome.DENY, ["emergency_suspension"]
    elif not context.state_fresh:
        outcome, reasons = PolicyOutcome.DEFER, ["stale_state"]
    elif request.capability not in context.capabilities:
        outcome, reasons = PolicyOutcome.DENY, [f"capability_not_granted:{request.capability}"]
    elif request.target and context.allowed_targets and request.target not in context.allowed_targets:
        outcome, reasons = PolicyOutcome.DENY, [f"target_not_allowed:{request.target or 'empty'}"]
    elif request.financial_amount > context.maximum_financial_amount:
        outcome, reasons = PolicyOutcome.DENY, ["financial_limit_exceeded"]
    elif request.financial_amount > context.review_financial_threshold:
        outcome, reasons = PolicyOutcome.REVIEW, ["financial_review_threshold"]
    elif request.origin == "model_proposal" and request.consequential and context.model_effects_require_review:
        outcome, reasons = PolicyOutcome.REVIEW, ["model_proposal_requires_review"]

    return PolicyDecision(
        policy_decision_id=str(uuid4()),
        request_id=request.request_id,
        outcome=outcome,
        policy_id=context.policy_id,
        decided_at=_now(),
        reasons=tuple(reasons),
    )
