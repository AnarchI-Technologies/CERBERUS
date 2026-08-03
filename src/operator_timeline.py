"""Sanitized operator timeline assembled from local decision evidence."""

from __future__ import annotations

from typing import Any

from audit_ledger import audit_rows
from runtime_state import action_postmortems, policy_shadow_records


def execution_timeline(limit: int = 60) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for row in audit_rows():
        audit = row.get("audit") if isinstance(row.get("audit"), dict) else {}
        events.append(
            {
                "recorded_at": str(audit.get("recorded_at") or ""),
                "kind": "execution",
                "action": str(row.get("operation") or "unknown")[:40],
                "status": str(row.get("execution_status") or "unknown")[:60],
                "policy": str(row.get("policy_outcome") or "unknown")[:20],
            }
        )
    for row in policy_shadow_records():
        policy = row.get("policy") if isinstance(row.get("policy"), dict) else {}
        action = row.get("selected_action") if isinstance(row.get("selected_action"), dict) else {}
        events.append(
            {
                "recorded_at": str(policy.get("decided_at") or ""),
                "kind": "policy",
                "action": str(action.get("type") or "unknown")[:40],
                "status": "enforced" if row.get("enforced") else "shadow",
                "policy": str(policy.get("outcome") or "unknown")[:20],
            }
        )
    for row in action_postmortems():
        expectation = str(row.get("expectation") or "")
        action = expectation.split(" should", 1)[0].strip() or "unknown"
        events.append(
            {
                "recorded_at": str(row.get("recorded_at") or ""),
                "kind": "outcome",
                "action": action[:40],
                "status": str(row.get("failure_category") or "unknown")[:60],
                "policy": "",
            }
        )
    events.sort(key=lambda item: (item["recorded_at"], item["kind"], item["action"]), reverse=True)
    return events[: max(1, min(limit, 200))]
