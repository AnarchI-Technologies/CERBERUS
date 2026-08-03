from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from audit_ledger import append_execution_audit
from execution_evidence import evaluate_execution_evidence
from v2_contracts import ActionRequest, Decision, Event, ExecutionResult, PolicyDecision, PolicyOutcome


def append(path: Path, sequence: int, game: str, *, status: str = "accepted") -> None:
    event = Event(f"event-{sequence}", "claw_royale.snapshot", "2026-07-19T00:00:00Z", game, {}, "official_server")
    decision = Decision(f"decision-{sequence}", event.event_id, "execute_free_pickup", "pickup", 1.0)
    request = ActionRequest(
        f"request-{sequence}", decision.decision_id, game, "agent", "game.action.execute",
        "claw_royale", "pickup", "item", consequential=False, idempotency_key=f"key-{sequence}",
    )
    policy = PolicyDecision(f"policy-{sequence}", request.request_id, PolicyOutcome.ALLOW, "free-v1", "2026-07-19T00:00:00Z")
    result = ExecutionResult(f"result-{sequence}", request.request_id, policy.policy_decision_id, "claw_royale", status, "2026-07-19T00:00:01Z")
    append_execution_audit(request, policy, result, event=event, decision=decision, path=path)


def test_sufficient_evidence_still_requires_operator_review() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        append(path, 1, "game-1")
        append(path, 2, "game-2")
        report = evaluate_execution_evidence(path, minimum_accepted=2, minimum_games=2, minimum_duration_hours=0)
    assert report["evidence_sufficient"] is True
    assert report["expansion_allowed"] is False
    assert report["requires_operator_review"] is True


def test_failure_blocks_evidence_gate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        append(path, 1, "game-1")
        append(path, 2, "game-2", status="failed")
        report = evaluate_execution_evidence(path, minimum_accepted=1, minimum_games=1, minimum_duration_hours=0)
    assert report["evidence_sufficient"] is False
    assert report["failures"] == 1
