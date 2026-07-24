"""Deterministic, observation-only post-mortems for Claw action outcomes."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from memory_system import scrub_scalar


FAILURE_CATEGORIES = {
    "target": ("target_blocked", "target blocked", "out of range", "not found"),
    "timing": ("cooldown", "cannot act", "turn", "too early", "too late"),
    "resources": ("insufficient ep", "not enough ep", "inventory_full", "inventory full"),
    "state": ("death zone", "stale", "invalid region", "already"),
    "provider": ("timeout", "connection", "server", "internal", "unavailable"),
    "authority": ("unauthorized", "forbidden", "whitelist", "signature", "version_mismatch"),
}


def _category(*, accepted: bool, detail: str) -> tuple[str, float]:
    if accepted:
        return "success", 0.99
    lowered = detail.lower()
    for category, markers in FAILURE_CATEGORIES.items():
        if any(marker in lowered for marker in markers):
            return category, 0.92
    return "unknown", 0.45


def _experiment(category: str, action_type: str) -> dict[str, Any]:
    variants = {
        "target": "require refreshed range and target-presence checks before the same action",
        "timing": "retry only after a fresh can-act or turn-advanced observation",
        "resources": "require one additional EP or inventory slot above the observed minimum",
        "state": "refresh the full board snapshot before repeating the route",
        "provider": "defer once and retry with the same idempotency key after health recovery",
        "authority": "defer to operator review; do not retry credentials or signing automatically",
        "unknown": "collect more matching outcomes without changing policy",
        "success": "retain the route and measure recurrence without increasing risk",
    }
    return {
        "status": "proposed",
        "hypothesis": variants[category],
        "variant": f"observe:{action_type}:{category}",
        "sample_size": 10 if category != "unknown" else 20,
        "guardrails": [
            "observation_only",
            "no_live_policy_change",
            "stop_on_lower_survival",
            "maximum_20_samples",
        ],
    }


def build_action_postmortem(
    *,
    action: dict[str, Any],
    payload: dict[str, Any],
    snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    error = scrub_scalar(payload.get("message") or payload.get("error") or payload.get("code"), limit=180)
    success_raw = payload.get("success", payload.get("ok"))
    accepted = not error and success_raw is not False
    action_type = scrub_scalar(action.get("type") or "unknown", limit=40)
    category, confidence = _category(accepted=accepted, detail=error)
    expectation_reason = scrub_scalar(action.get("reason"), limit=180)
    expectation = f"{action_type} should be accepted"
    if expectation_reason:
        expectation += f" because {expectation_reason}"
    observed = f"accepted {action_type}" if accepted else f"rejected {action_type}: {error or 'unspecified error'}"
    return {
        "postmortem_id": str(uuid4()),
        "schema_version": "cerberus.postmortem.v1",
        "recorded_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "game_id": scrub_scalar(snapshot.get("game_id"), limit=80),
        "turn": int(snapshot.get("turn") or 0),
        "action_type": action_type,
        "expectation": expectation,
        "observed_outcome": observed,
        "failure_category": category,
        "confidence": confidence,
        "proposed_experiment": _experiment(category, action_type),
        "activated": False,
    }
