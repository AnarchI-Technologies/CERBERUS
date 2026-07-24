"""Fail-closed semantic evidence gate for local model alias promotion."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    approved: bool
    reasons: tuple[str, ...]
    fixture_count: int
    semantic_success_rate: float


def evaluate_model_promotion(
    alias: dict[str, Any],
    evaluation: dict[str, Any],
    *,
    minimum_fixtures: int = 20,
    minimum_semantic_rate: float = 0.95,
) -> PromotionDecision:
    model = str(alias.get("model") or "")
    digest = str(alias.get("digest") or "")
    results = evaluation.get("results") if isinstance(evaluation.get("results"), list) else []
    matched = [
        result
        for result in results
        if isinstance(result, dict)
        and str(result.get("model") or "") == model
        and str(result.get("digest") or "") == digest
    ]
    schema_passes = sum(1 for result in matched if result.get("schema_valid") is True)
    semantic_passes = sum(
        1 for result in matched if result.get("schema_valid") is True and result.get("semantic_match") is True
    )
    rate = semantic_passes / max(1, len(matched))
    reasons: list[str] = []
    if not model or not digest:
        reasons.append("unpinned_alias")
    if len(matched) < minimum_fixtures:
        reasons.append("insufficient_semantic_fixtures")
    if schema_passes != len(matched):
        reasons.append("schema_failures_present")
    if rate < minimum_semantic_rate:
        reasons.append("semantic_threshold_not_met")
    if str(evaluation.get("promotion_decision") or "").lower() != "approved":
        reasons.append("evaluation_not_approved")
    return PromotionDecision(
        approved=not reasons,
        reasons=tuple(reasons),
        fixture_count=len(matched),
        semantic_success_rate=rate,
    )
