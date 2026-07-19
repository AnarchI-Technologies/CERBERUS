"""Policy-bound, idempotent execution coordinator for isolated adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from audit_ledger import append_execution_audit
from runtime_state import finalize_execution, reserve_execution
from v2_contracts import ActionRequest, ExecutionResult, PolicyDecision, PolicyOutcome, contract_dict


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
    request: ActionRequest, policy: PolicyDecision, result: ExecutionResult
) -> ExecutionResult:
    append_execution_audit(request, policy, result)
    return result


async def execute_authorized(
    request: ActionRequest,
    policy: PolicyDecision,
    adapter: Callable[[], Awaitable[dict[str, Any]]],
    *,
    timeout_seconds: float = 5.0,
) -> ExecutionResult:
    if policy.request_id != request.request_id:
        return _audited(request, policy, _result(request, policy, "policy_mismatch", detail="policy request ID mismatch"))
    if policy.outcome is not PolicyOutcome.ALLOW:
        return _audited(request, policy, _result(request, policy, "policy_blocked", detail=",".join(policy.reasons)))
    if not request.idempotency_key:
        return _audited(request, policy, _result(request, policy, "invalid_request", detail="missing idempotency key"))
    if not reserve_execution(request.idempotency_key, request.request_id):
        return _audited(request, policy, _result(request, policy, "duplicate_suppressed", detail="idempotency key already reserved"))
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
    return _audited(request, policy, result)
