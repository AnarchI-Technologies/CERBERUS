from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from audit_ledger import append_execution_audit
from operator_timeline import execution_timeline
from runtime_state import append_action_postmortem, append_policy_shadow, overridden_memory_dir
from v2_contracts import ActionRequest, ExecutionResult, PolicyDecision, PolicyOutcome


def test_timeline_is_sanitized_and_combines_policy_execution_and_outcome() -> None:
    request = ActionRequest(
        request_id="request-private", decision_id="decision-private", correlation_id="game-private",
        actor_id="hellion", capability="game.action.execute", provider="claw_royale",
        operation="pickup", target="target-private", consequential=False, idempotency_key="key-private",
    )
    policy = PolicyDecision(
        policy_decision_id="policy-private", request_id=request.request_id, outcome=PolicyOutcome.ALLOW,
        policy_id="claw-free-action-v1", decided_at="2026-07-19T01:00:00Z",
    )
    result = ExecutionResult(
        result_id="result-private", request_id=request.request_id,
        policy_decision_id=policy.policy_decision_id, provider="claw_royale", status="accepted",
        occurred_at="2026-07-19T01:00:01Z",
    )
    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        append_execution_audit(request, policy, result)
        append_policy_shadow(
            {
                "policy": {"decided_at": "2026-07-19T01:00:00Z", "outcome": "ALLOW"},
                "selected_action": {"type": "pickup", "itemId": "target-private"},
                "enforced": True,
            }
        )
        append_action_postmortem(
            {"recorded_at": "2026-07-19T01:00:02Z", "expectation": "pickup should be accepted", "failure_category": "success"}
        )
        timeline = execution_timeline()

    assert {item["kind"] for item in timeline} == {"execution", "policy", "outcome"}
    rendered = str(timeline)
    assert "target-private" not in rendered
    assert "game-private" not in rendered
    assert "request-private" not in rendered
