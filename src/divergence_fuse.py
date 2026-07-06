"""Track certainty-versus-outcome divergence and trip an epistemic fuse when needed."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from external_wisdom import divergence_policy
from memory_system import scrub_scalar
from runtime_state import memory_dir, read_json, write_json


def divergence_ledger_file() -> Path:
    return memory_dir() / "divergence_fuse.json"


def divergence_ledger(limit: int = 200) -> list[dict[str, Any]]:
    rows = read_json(divergence_ledger_file()).get("entries", [])
    if not isinstance(rows, list):
        return []
    return [item for item in rows if isinstance(item, dict)][-max(1, limit):]


def record_divergence_observation(
    *,
    kind: str,
    subject: str,
    asserted_confidence: float,
    outcome_quality: float,
    note: str = "",
    source: str = "",
    evidence_anchor: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = divergence_ledger(limit=limit)
    claimed = max(0.0, min(1.0, float(asserted_confidence)))
    quality = max(0.0, min(1.0, float(outcome_quality)))
    rows.append(
        {
            "created_at": int(time.time()),
            "kind": scrub_scalar(kind, limit=32),
            "subject": scrub_scalar(subject, limit=48),
            "asserted_confidence": round(claimed, 4),
            "outcome_quality": round(quality, 4),
            "gap": round(max(0.0, claimed - quality), 4),
            "note": scrub_scalar(note, limit=220),
            "source": scrub_scalar(source, limit=80),
            "evidence_anchor": scrub_scalar(evidence_anchor, limit=32),
        }
    )
    rows = rows[-max(1, limit):]
    write_json(divergence_ledger_file(), {"entries": rows, "updated_at": int(time.time())})
    return rows


def divergence_fuse_status(limit: int = 80) -> dict[str, Any]:
    policy = divergence_policy()
    rows = divergence_ledger(limit=limit)
    threshold = float(policy.get("threshold", 0.28) or 0.28)
    min_samples = int(policy.get("min_samples", 4) or 4)
    if not rows:
        return {
            "ok": True,
            "tripped": False,
            "threshold": threshold,
            "min_samples": min_samples,
            "samples": 0,
            "average_gap": 0.0,
            "block_actions": policy.get("block_actions", []),
        }
    average_gap = round(sum(float(item.get("gap") or 0.0) for item in rows) / max(1, len(rows)), 4)
    tripped = len(rows) >= min_samples and average_gap >= threshold
    return {
        "ok": not tripped,
        "tripped": tripped,
        "threshold": threshold,
        "min_samples": min_samples,
        "samples": len(rows),
        "average_gap": average_gap,
        "block_actions": policy.get("block_actions", []),
        "latest": rows[-5:],
        "epistemic_reset_required": bool(policy.get("epistemic_reset_required_on_trip", True)) and tripped,
    }
