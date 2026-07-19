from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from execution_coordinator import execute_authorized
from audit_ledger import audit_rows, verify_audit_ledger
from runtime_state import execution_ledger_records, overridden_memory_dir
from v2_contracts import ActionRequest, PolicyDecision, PolicyOutcome


def request() -> ActionRequest:
    return ActionRequest(
        request_id="request-1",
        decision_id="decision-1",
        correlation_id="game-1",
        actor_id="hellion",
        capability="game.action.execute",
        provider="claw_royale",
        operation="broadcast",
        consequential=False,
        idempotency_key="game-1:turn-4:broadcast:status",
    )


def policy(outcome: PolicyOutcome = PolicyOutcome.ALLOW) -> PolicyDecision:
    return PolicyDecision(
        policy_decision_id="policy-1",
        request_id="request-1",
        outcome=outcome,
        policy_id="claw-free-action-v1",
        decided_at="2026-07-19T00:00:00Z",
    )


def test_executes_allowed_request_once_and_persists_result() -> None:
    calls = 0

    async def adapter():  # type: ignore[no-untyped-def]
        nonlocal calls
        calls += 1
        return {"code": "sent"}

    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        first = asyncio.run(execute_authorized(request(), policy(), adapter))
        second = asyncio.run(execute_authorized(request(), policy(), adapter))
        ledger = execution_ledger_records()
        audit = audit_rows()

    assert first.status == "accepted"
    assert second.status == "duplicate_suppressed"
    assert calls == 1
    assert ledger[0]["status"] == "accepted"
    assert [item["execution_status"] for item in audit] == ["accepted", "duplicate_suppressed"]
    assert verify_audit_ledger()["ok"] is True


def test_denied_policy_never_calls_adapter_or_reserves_key() -> None:
    called = False

    async def adapter():  # type: ignore[no-untyped-def]
        nonlocal called
        called = True
        return {}

    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        result = asyncio.run(execute_authorized(request(), policy(PolicyOutcome.DENY), adapter))
        ledger = execution_ledger_records()

    assert result.status == "policy_blocked"
    assert not called
    assert ledger == []
