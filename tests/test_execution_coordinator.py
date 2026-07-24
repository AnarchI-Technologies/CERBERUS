from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from execution_coordinator import execute_authorized, reconcile_reserved_free_actions
from claw_runtime import coordinate_free_action_send
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


def test_runtime_coordinates_visible_pickup_once() -> None:
    from turn_state_model import TurnState

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, value: str) -> None:
            self.sent.append(value)

    state = TurnState.from_snapshot(
        {
            "gameId": "game-1",
            "turn": 7,
            "view": {
                "self": {"id": "hellion", "hp": 100, "ep": 5},
                "currentRegion": {"id": "r1", "items": [{"id": "relic-1", "typeId": "relic"}]},
            },
        }
    )
    socket = Socket()
    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        first, _ = asyncio.run(coordinate_free_action_send(socket, state, {"type": "pickup", "itemId": "relic-1"}))
        second, _ = asyncio.run(coordinate_free_action_send(socket, state, {"type": "pickup", "itemId": "relic-1"}))
        audit = audit_rows()

    assert first.status == "accepted"
    assert second.status == "duplicate_suppressed"
    assert len(socket.sent) == 1
    assert audit[0]["audit"]["event_id"].startswith("claw-event-")
    assert audit[0]["audit"]["decision_id"].startswith("claw-decision-")
    assert audit[0]["event"] == {
        "source": "claw_royale.snapshot",
        "observed_at": audit[0]["event"]["observed_at"],
        "trust": "official_server",
    }
    assert audit[0]["decision"]["assisted_by_model"] is False
    assert audit[0]["decision"]["selected_route"] == "pickup"


def test_reconciles_crash_left_pickup_without_allowing_retry() -> None:
    from runtime_state import reserve_execution
    from turn_state_model import TurnState

    state = TurnState.from_snapshot(
        {
            "view": {
                "self": {
                    "id": "hellion",
                    "hp": 100,
                    "ep": 5,
                    "inventory": [{"id": "relic-1", "typeId": "relic"}],
                },
                "currentRegion": {"id": "r1", "items": []},
            }
        }
    )
    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        assert reserve_execution(
            "key-pickup",
            "request-pickup",
            metadata={"operation": "pickup", "target": "relic-1"},
        )
        reconciled = reconcile_reserved_free_actions(state)
        ledger = execution_ledger_records()

    assert reconciled == [{"status": "reconciled_observed", "operation": "pickup", "target": "relic-1"}]
    assert ledger[0]["status"] == "reconciled_observed"


def test_unobservable_reserved_broadcast_becomes_indeterminate() -> None:
    from runtime_state import reserve_execution
    from turn_state_model import TurnState

    state = TurnState.from_snapshot({"view": {"self": {"id": "hellion", "hp": 100, "ep": 5}}})
    with tempfile.TemporaryDirectory() as tmp, overridden_memory_dir(tmp):
        assert reserve_execution(
            "key-broadcast", "request-broadcast", metadata={"operation": "broadcast", "target": ""}
        )
        reconciled = reconcile_reserved_free_actions(state)

    assert reconciled[0]["status"] == "indeterminate_no_retry"
