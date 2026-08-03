"""Versioned, provider-neutral contracts for the CERBERUS v2 boundary."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SCHEMA_VERSION = "cerberus.v2.1"


class PolicyOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    REVIEW = "REVIEW"
    DEFER = "DEFER"


@dataclass(frozen=True, slots=True)
class Event:
    event_id: str
    source: str
    observed_at: str
    correlation_id: str
    payload: dict[str, Any]
    trust: str = "external"
    freshness_seconds: float = 0.0
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Decision:
    decision_id: str
    event_id: str
    intent: str
    selected_route: str
    confidence: float
    evidence_refs: tuple[str, ...] = ()
    assisted_by_model: bool = False
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ActionRequest:
    request_id: str
    decision_id: str
    correlation_id: str
    actor_id: str
    capability: str
    provider: str
    operation: str
    target: str = ""
    environment: str = "production"
    consequential: bool = True
    financial_amount: float = 0.0
    origin: str = "deterministic"
    idempotency_key: str = ""
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    policy_decision_id: str
    request_id: str
    outcome: PolicyOutcome
    policy_id: str
    decided_at: str
    reasons: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    result_id: str
    request_id: str
    policy_decision_id: str
    provider: str
    status: str
    occurred_at: str
    retryable: bool = False
    provider_code: str = ""
    sanitized_detail: str = ""
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    record_id: str
    classification: str
    source_ref: str
    recorded_at: str
    content: str
    retention: str
    confidence: float = 1.0
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AuditRecord:
    audit_id: str
    correlation_id: str
    event_id: str
    decision_id: str
    request_id: str
    policy_decision_id: str
    execution_result_id: str = ""
    recorded_at: str = ""
    schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class PolicyContext:
    policy_id: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    allowed_targets: frozenset[str] = field(default_factory=frozenset)
    suspended: bool = False
    state_fresh: bool = True
    review_financial_threshold: float = 0.0
    maximum_financial_amount: float = 0.0
    model_effects_require_review: bool = True


def contract_dict(value: Any) -> dict[str, Any]:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if isinstance(item, Enum):
            payload[key] = item.value
        elif isinstance(item, (set, frozenset, tuple)):
            payload[key] = list(item)
    return payload


def validate_action_request(request: ActionRequest) -> tuple[str, ...]:
    errors: list[str] = []
    for field_name in ("request_id", "decision_id", "correlation_id", "actor_id", "capability", "provider", "operation"):
        if not str(getattr(request, field_name)).strip():
            errors.append(f"missing:{field_name}")
    if request.financial_amount < 0:
        errors.append("invalid:financial_amount")
    if request.origin not in {"deterministic", "model_proposal", "operator"}:
        errors.append("invalid:origin")
    return tuple(errors)
