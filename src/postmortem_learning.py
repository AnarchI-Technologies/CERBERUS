"""Deterministic, observation-only post-match learning records.

These records are deliberately not consumed by the decision engine.  They are
evidence for a later, separately gated learning step.
"""

from __future__ import annotations

from typing import Any

from memory_system import stable_hash, utc_now
from mongo_memory import configured_longterm_memory_store


FAILURE_CATEGORIES = {
    "action_rejection",
    "combat_or_positioning",
    "economy",
    "insufficient_evidence",
    "runtime",
}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _failure_category(*, balance_delta: float, terminal_error: str, action_audit: list[dict[str, Any]]) -> tuple[str, float]:
    error = terminal_error.lower()
    if any(term in error for term in ("socket", "timeout", "connection", "runtime")):
        return "runtime", 0.9
    rejected = [
        row for row in action_audit
        if row.get("kind") == "action_result"
        and isinstance(row.get("outcome"), dict)
        and row["outcome"].get("ok") is False
    ]
    if rejected:
        return "action_rejection", min(0.9, 0.65 + (0.05 * len(rejected)))
    if balance_delta < 0:
        return "economy", 0.72
    if any(term in error for term in ("dead", "death", "eliminat", "defeat", "game ended", "not running")):
        return "combat_or_positioning", 0.62
    return "insufficient_evidence", 0.35


def _experiment(category: str) -> dict[str, Any]:
    variants = {
        "action_rejection": ("rejection_backoff", "For the next 10 comparable matches, do not retry an identically rejected action in the same turn."),
        "combat_or_positioning": ("earlier_disengagement", "For the next 10 comparable matches, compare the current policy with disengaging one decision point earlier."),
        "economy": ("negative_delta_holdout", "For the next 10 comparable matches, compare the current policy with avoiding the last optional spend pattern."),
        "runtime": ("runtime_reliability", "For the next 10 sessions, measure completion rate before changing strategy."),
        "insufficient_evidence": ("evidence_collection", "For the next 10 matches, collect terminal placement, killer, HP, EP, and final action without changing strategy."),
    }
    key, hypothesis = variants[category]
    return {"key": key, "sample_size": 10, "changes_live_policy": False, "hypothesis": hypothesis}


def build_postmortem(
    *,
    game_id: str,
    last_action: dict[str, Any] | None,
    balance_delta: Any = 0,
    terminal_error: str = "",
    action_audit: list[dict[str, Any]] | None = None,
    terminal_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the same record for the same evidence (apart from ``recorded_at``)."""
    action = last_action if isinstance(last_action, dict) else {}
    audit = action_audit if isinstance(action_audit, list) else []
    terminal = terminal_evidence if isinstance(terminal_evidence, dict) else {}
    delta = _number(balance_delta)
    category, confidence = _failure_category(
        balance_delta=delta,
        terminal_error=str(terminal_error or ""),
        action_audit=audit,
    )
    expectation = str(action.get("reason") or "No explicit expectation was recorded for the final action")[:240]
    outcome = f"match ended; balance delta={delta:.6f}"
    if terminal_error:
        outcome += f"; terminal signal={str(terminal_error)[:160]}"
    evidence = {
        "game_id": str(game_id or "unknown")[:96],
        "last_action_type": str(action.get("type") or "unknown")[:48],
        "balance_delta": delta,
        "terminal_error": str(terminal_error or "")[:160],
        "placement": terminal.get("placement"),
        "killer_id": str(terminal.get("killer_id") or "")[:96],
        "killer_name": str(terminal.get("killer_name") or "")[:96],
        "final_hp": terminal.get("final_hp"),
        "final_ep": terminal.get("final_ep"),
        "alive": terminal.get("alive"),
        "alive_count": terminal.get("alive_count"),
        "recent_actions": terminal.get("recent_actions", [])[:5]
        if isinstance(terminal.get("recent_actions"), list) else [],
    }
    return {
        "schema_version": 1,
        "game": "claw_royale",
        "game_id": evidence["game_id"],
        "expectation": expectation,
        "observed_outcome": outcome,
        "likely_failure_category": category,
        "confidence": confidence,
        "proposed_experiment": _experiment(category),
        "evidence": evidence,
        "evidence_hash": stable_hash(evidence, length=24),
        "recorded_at": utc_now(),
    }


def record_postmortem(record: dict[str, Any], *, store: Any | None = None) -> str:
    """Persist a typed record without feeding it into gameplay policy."""
    category = str(record.get("likely_failure_category") or "insufficient_evidence")
    if category not in FAILURE_CATEGORIES:
        raise ValueError(f"unknown failure category: {category}")
    backend = store or configured_longterm_memory_store()
    experiment = record.get("proposed_experiment") if isinstance(record.get("proposed_experiment"), dict) else {}
    text = (
        f"expectation={record.get('expectation', '')}; "
        f"outcome={record.get('observed_outcome', '')}; "
        f"category={category}; experiment={experiment.get('hypothesis', '')}"
    )
    return backend.remember(
        kind="postmortem",
        scope="claw_royale",
        key=str(record.get("evidence_hash") or stable_hash(record, length=24)),
        text=text,
        confidence=float(record.get("confidence") or 0),
        importance=65,
        metadata=record,
    )
