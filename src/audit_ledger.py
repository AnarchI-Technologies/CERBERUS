"""Hash-linked append-only audit ledger for v2 execution boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime_state import memory_dir
from v2_contracts import ActionRequest, AuditRecord, ExecutionResult, PolicyDecision, contract_dict


def audit_ledger_file() -> Path:
    return memory_dir() / "audit_ledger.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def audit_rows(path: Path | None = None) -> list[dict[str, Any]]:
    source = path or audit_ledger_file()
    if not source.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in source.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    except (OSError, json.JSONDecodeError):
        return []
    return rows


def append_execution_audit(
    request: ActionRequest,
    policy: PolicyDecision,
    result: ExecutionResult,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    target = path or audit_ledger_file()
    if target.exists():
        verification = verify_audit_ledger(target)
        if not verification.get("ok"):
            raise RuntimeError(f"audit ledger verification failed: {verification.get('error')}")
    prior = audit_rows(target)
    previous_hash = str(prior[-1].get("record_hash") or "") if prior else ""
    audit = AuditRecord(
        audit_id=str(uuid4()),
        correlation_id=request.correlation_id,
        event_id="",
        decision_id=request.decision_id,
        request_id=request.request_id,
        policy_decision_id=policy.policy_decision_id,
        execution_result_id=result.result_id,
        recorded_at=_now(),
    )
    row = {
        "sequence": len(prior) + 1,
        "previous_hash": previous_hash,
        "audit": contract_dict(audit),
        "operation": request.operation[:80],
        "provider": request.provider[:80],
        "policy_outcome": policy.outcome.value,
        "execution_status": result.status[:80],
        "retryable": result.retryable,
    }
    row["record_hash"] = _hash(row)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def verify_audit_ledger(path: Path | None = None) -> dict[str, Any]:
    rows = audit_rows(path)
    previous_hash = ""
    for index, row in enumerate(rows, start=1):
        claimed = str(row.get("record_hash") or "")
        unsigned = {key: value for key, value in row.items() if key != "record_hash"}
        if row.get("sequence") != index:
            return {"ok": False, "records": len(rows), "error": f"sequence:{index}"}
        if str(row.get("previous_hash") or "") != previous_hash:
            return {"ok": False, "records": len(rows), "error": f"previous_hash:{index}"}
        if claimed != _hash(unsigned):
            return {"ok": False, "records": len(rows), "error": f"record_hash:{index}"}
        previous_hash = claimed
    return {"ok": True, "records": len(rows), "head_hash": previous_hash}
