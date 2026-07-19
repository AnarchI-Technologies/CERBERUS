from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from model_promotion import evaluate_model_promotion


def alias() -> dict:
    return {"model": "local:test", "digest": "pinned"}


def result(*, semantic: bool = True, schema: bool = True) -> dict:
    return {
        "model": "local:test",
        "digest": "pinned",
        "schema_valid": schema,
        "semantic_match": semantic,
    }


def test_schema_validity_without_semantic_match_never_promotes() -> None:
    evaluation = {"promotion_decision": "approved", "results": [result(semantic=False) for _ in range(20)]}
    decision = evaluate_model_promotion(alias(), evaluation)

    assert not decision.approved
    assert decision.semantic_success_rate == 0
    assert "semantic_threshold_not_met" in decision.reasons


def test_requires_sufficient_pinned_semantic_fixtures_and_explicit_approval() -> None:
    too_small = evaluate_model_promotion(
        alias(), {"promotion_decision": "approved", "results": [result() for _ in range(19)]}
    )
    wrong_digest = evaluate_model_promotion(
        alias(),
        {
            "promotion_decision": "approved",
            "results": [{**result(), "digest": "other"} for _ in range(20)],
        },
    )
    rejected = evaluate_model_promotion(
        alias(), {"promotion_decision": "rejected", "results": [result() for _ in range(20)]}
    )

    assert "insufficient_semantic_fixtures" in too_small.reasons
    assert wrong_digest.fixture_count == 0
    assert "evaluation_not_approved" in rejected.reasons


def test_approved_semantic_fixture_set_can_pass_gate() -> None:
    evaluation = {"promotion_decision": "approved", "results": [result() for _ in range(20)]}
    decision = evaluate_model_promotion(alias(), evaluation)

    assert decision.approved
    assert decision.semantic_success_rate == 1.0


def test_existing_qwen_evaluation_remains_rejected() -> None:
    aliases = json.loads((ROOT / "models" / "aliases.json").read_text(encoding="utf-8"))["aliases"]
    evaluation = json.loads(
        (ROOT / "models" / "evaluations" / "2026-07-18-windows-qwen3.json").read_text(encoding="utf-8")
    )

    assert not evaluate_model_promotion(aliases["cerberus-fast"], evaluation).approved
    assert not evaluate_model_promotion(aliases["cerberus-reason"], evaluation).approved
