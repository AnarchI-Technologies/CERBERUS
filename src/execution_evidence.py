"""Deterministic production-evidence gate for expanding execution authority."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from audit_ledger import audit_rows, verify_audit_ledger


SAFE_OPERATIONS = frozenset({"pickup", "equip", "broadcast"})
FAILURE_STATUSES = frozenset({"failed", "invalid_request", "policy_mismatch"})


def _timestamp(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def evaluate_execution_evidence(
    path: str | Path | None = None,
    *,
    minimum_accepted: int = 20,
    minimum_games: int = 5,
    minimum_duration_hours: float = 24.0,
) -> dict[str, Any]:
    target = Path(path) if path is not None else None
    verification = verify_audit_ledger(target)
    rows = audit_rows(target)
    eligible = [row for row in rows if str(row.get("operation")) in SAFE_OPERATIONS]
    accepted = [row for row in eligible if row.get("execution_status") == "accepted"]
    failures = [row for row in eligible if str(row.get("execution_status")) in FAILURE_STATUSES]
    bypasses = [row for row in accepted if row.get("policy_outcome") != "ALLOW"]
    incomplete = [
        row for row in accepted
        if not row.get("audit", {}).get("event_id")
        or not row.get("audit", {}).get("decision_id")
        or row.get("event", {}).get("trust") != "official_server"
        or row.get("decision", {}).get("assisted_by_model") is not False
    ]
    games = {
        str(row.get("audit", {}).get("correlation_id") or "")
        for row in accepted
        if row.get("audit", {}).get("correlation_id")
    }
    times = [value for value in (_timestamp(row.get("audit", {}).get("recorded_at")) for row in accepted) if value]
    duration_hours = (max(times) - min(times)).total_seconds() / 3600.0 if len(times) >= 2 else 0.0
    sufficient = bool(
        verification.get("ok")
        and len(accepted) >= minimum_accepted
        and len(games) >= minimum_games
        and duration_hours >= minimum_duration_hours
        and not failures
        and not bypasses
        and not incomplete
    )
    return {
        "ledger_valid": bool(verification.get("ok")),
        "eligible_records": len(eligible),
        "accepted": len(accepted),
        "distinct_games": len(games),
        "duration_hours": round(duration_hours, 3),
        "failures": len(failures),
        "policy_bypasses": len(bypasses),
        "incomplete_contract_chains": len(incomplete),
        "evidence_sufficient": sufficient,
        "expansion_allowed": False,
        "requires_operator_review": True,
    }
