from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from policy_engine import evaluate_action_request
from v2_contracts import ActionRequest, PolicyContext, PolicyOutcome, SCHEMA_VERSION, contract_dict


def request(**overrides) -> ActionRequest:  # type: ignore[no-untyped-def]
    values = {
        "request_id": "req-1",
        "decision_id": "decision-1",
        "correlation_id": "corr-1",
        "actor_id": "hellion",
        "capability": "game.action.execute",
        "provider": "claw_royale",
        "operation": "move",
        "target": "region-safe",
        "consequential": True,
        "idempotency_key": "idem-1",
    }
    values.update(overrides)
    return ActionRequest(**values)


def context(**overrides) -> PolicyContext:  # type: ignore[no-untyped-def]
    values = {
        "policy_id": "claw-production-v1",
        "capabilities": frozenset({"game.action.execute"}),
        "allowed_targets": frozenset({"region-safe"}),
        "maximum_financial_amount": 100.0,
        "review_financial_threshold": 25.0,
    }
    values.update(overrides)
    return PolicyContext(**values)


def test_allows_valid_deterministic_request() -> None:
    result = evaluate_action_request(request(), context())
    assert result.outcome is PolicyOutcome.ALLOW
    assert result.request_id == "req-1"
    assert contract_dict(result)["schema_version"] == SCHEMA_VERSION


def test_denies_missing_capability_and_unknown_target() -> None:
    denied_capability = evaluate_action_request(request(), context(capabilities=frozenset()))
    denied_target = evaluate_action_request(request(target="region-hostile"), context())
    assert denied_capability.outcome is PolicyOutcome.DENY
    assert denied_target.outcome is PolicyOutcome.DENY


def test_emergency_suspension_denies_before_execution() -> None:
    result = evaluate_action_request(request(), context(suspended=True))
    assert result.outcome is PolicyOutcome.DENY
    assert result.reasons == ("emergency_suspension",)


def test_stale_state_defers_instead_of_guessing() -> None:
    result = evaluate_action_request(request(), context(state_fresh=False))
    assert result.outcome is PolicyOutcome.DEFER


def test_financial_limits_review_then_deny() -> None:
    review = evaluate_action_request(request(financial_amount=30), context())
    deny = evaluate_action_request(request(financial_amount=101), context())
    assert review.outcome is PolicyOutcome.REVIEW
    assert deny.outcome is PolicyOutcome.DENY


def test_model_proposal_cannot_receive_direct_effect_authority() -> None:
    result = evaluate_action_request(request(origin="model_proposal"), context())
    assert result.outcome is PolicyOutcome.REVIEW
    assert result.reasons == ("model_proposal_requires_review",)


def test_malformed_request_fails_closed() -> None:
    result = evaluate_action_request(request(actor_id="", financial_amount=-1), context())
    assert result.outcome is PolicyOutcome.DENY
    assert "missing:actor_id" in result.reasons
