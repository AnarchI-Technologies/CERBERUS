"""Policy-bound, idempotent execution coordinator for isolated adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from audit_ledger import append_execution_audit
from runtime_state import execution_ledger_records, finalize_execution, reserve_execution
from turn_state_model import TurnState
from v2_contracts import ActionRequest, Decision, Event, ExecutionResult, PolicyDecision, PolicyOutcome, contract_dict


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _result(
    request: ActionRequest,
    policy: PolicyDecision,
    status: str,
    *,
    retryable: bool = False,
    provider_code: str = "",
    detail: str = "",
) -> ExecutionResult:
    return ExecutionResult(
        result_id=str(uuid4()),
        request_id=request.request_id,
        policy_decision_id=policy.policy_decision_id,
        provider=request.provider,
        status=status,
        occurred_at=_now(),
        retryable=retryable,
        provider_code=provider_code[:80],
        sanitized_detail=detail[:240],
    )


def _audited(
    request: ActionRequest,
    policy: PolicyDecision,
    result: ExecutionResult,
    *,
    event: Event | None = None,
    decision: Decision | None = None,
) -> ExecutionResult:
    append_execution_audit(request, policy, result, event=event, decision=decision)
    return result


async def execute_authorized(
    request: ActionRequest,
    policy: PolicyDecision,
    adapter: Callable[[], Awaitable[dict[str, Any]]],
    *,
    timeout_seconds: float = 5.0,
    event: Event | None = None,
    decision: Decision | None = None,
) -> ExecutionResult:
    if policy.request_id != request.request_id:
        return _audited(request, policy, _result(request, policy, "policy_mismatch", detail="policy request ID mismatch"), event=event, decision=decision)
    if policy.outcome is not PolicyOutcome.ALLOW:
        return _audited(request, policy, _result(request, policy, "policy_blocked", detail=",".join(policy.reasons)), event=event, decision=decision)
    if not request.idempotency_key:
        return _audited(request, policy, _result(request, policy, "invalid_request", detail="missing idempotency key"), event=event, decision=decision)
    if not reserve_execution(
        request.idempotency_key,
        request.request_id,
        metadata={
            "provider": request.provider,
            "operation": request.operation,
            "target": request.target,
            "correlation_id": request.correlation_id,
        },
    ):
        return _audited(request, policy, _result(request, policy, "duplicate_suppressed", detail="idempotency key already reserved"), event=event, decision=decision)
    try:
        response = await asyncio.wait_for(adapter(), timeout=max(0.1, min(timeout_seconds, 30.0)))
        result = _result(
            request,
            policy,
            "accepted",
            provider_code=str(response.get("code") or "accepted"),
            detail=str(response.get("detail") or ""),
        )
    except TimeoutError:
        result = _result(request, policy, "timeout", retryable=True, detail="adapter deadline exceeded")
    except Exception as exc:
        result = _result(request, policy, "failed", retryable=False, detail=type(exc).__name__)
    finalize_execution(request.idempotency_key, contract_dict(result))
    return _audited(request, policy, result, event=event, decision=decision)


def reconcile_reserved_free_actions(state: TurnState) -> list[dict[str, str]]:
    """Resolve crash-left reservations without ever guessing that retry is safe."""
    inventory_ids = {str(item.get("id") or "") for item in state.inventory if isinstance(item, dict)}
    ground_ids = {str(item.get("id") or "") for item in state.local_ground_items()}
    equipped_ids = {
        str(value.get("id") or value.get("instanceId") or "")
        for key, value in state.self.raw.items()
        if str(key).lower().startswith("equipped") and isinstance(value, dict)
    }
    reconciled: list[dict[str, str]] = []
    for row in execution_ledger_records():
        if row.get("status") != "reserved":
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        operation = str(metadata.get("operation") or "")
        target = str(metadata.get("target") or "")
        observed = (
            operation == "pickup" and target in inventory_ids and target not in ground_ids
        ) or (operation == "equip" and target in equipped_ids)
        status = "reconciled_observed" if observed else "indeterminate_no_retry"
        result = {"status": status, "operation": operation, "target": target}
        finalize_execution(str(row.get("idempotency_key") or ""), result)
        reconciled.append(result)
    return reconciled
