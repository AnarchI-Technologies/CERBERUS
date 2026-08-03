from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from audit_ledger import append_execution_audit, audit_rows, verify_audit_ledger
from v2_contracts import ActionRequest, ExecutionResult, PolicyDecision, PolicyOutcome


def contracts(sequence: int):  # type: ignore[no-untyped-def]
    request = ActionRequest(
        request_id=f"request-{sequence}", decision_id=f"decision-{sequence}", correlation_id="game-safe",
        actor_id="hellion", capability="game.action.execute", provider="claw_royale",
        operation="broadcast", consequential=False, idempotency_key=f"key-{sequence}",
    )
    policy = PolicyDecision(
        policy_decision_id=f"policy-{sequence}", request_id=request.request_id,
        outcome=PolicyOutcome.ALLOW, policy_id="claw-free-action-v1", decided_at="2026-07-19T00:00:00Z",
    )
    result = ExecutionResult(
        result_id=f"result-{sequence}", request_id=request.request_id,
        policy_decision_id=policy.policy_decision_id, provider="claw_royale", status="accepted",
        occurred_at="2026-07-19T00:00:01Z",
    )
    return request, policy, result


def test_audit_ledger_links_records_and_detects_tampering() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "audit.jsonl"
        append_execution_audit(*contracts(1), path=path)
        append_execution_audit(*contracts(2), path=path)
        rows = audit_rows(path)
        valid = verify_audit_ledger(path)
        rows[0]["execution_status"] = "tampered"
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        invalid = verify_audit_ledger(path)

    assert valid["ok"] is True
    assert valid["records"] == 2
    assert rows[1]["previous_hash"] == rows[0]["record_hash"]
    assert invalid["ok"] is False
    assert invalid["error"] == "record_hash:1"
