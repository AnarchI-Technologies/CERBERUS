from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from strategy_experiments import ExperimentDefinition, assignment, evaluate, record_outcome


def definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id="exploration-v1",
        control_agent_id="hellion",
        candidate_agent_id="agent-lab-1",
        control_variant="baseline",
        candidate_variant="relic-sprint",
        minimum_games_per_variant=2,
        maximum_games_per_variant=4,
    )


def test_assignments_require_distinct_enrolled_agents() -> None:
    spec = definition()
    assert assignment(spec, "hellion") == "baseline"
    assert assignment(spec, "agent-lab-1") == "relic-sprint"
    try:
        assignment(spec, "unregistered")
    except ValueError:
        pass
    else:
        raise AssertionError("unregistered agent should be rejected")


def test_candidate_requires_bounded_evidence_and_operator_review() -> None:
    spec = definition()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "experiments.json"
        for index in range(2):
            record_outcome(
                spec, agent_id="hellion", game_id=f"control-{index}", survived=True,
                placement=6, objective_progress=1, reward=100, path=path,
            )
            record_outcome(
                spec, agent_id="agent-lab-1", game_id=f"candidate-{index}", survived=True,
                placement=5, objective_progress=3, reward=100, path=path,
            )
        report = evaluate(spec, path=path)

    assert report["recommendation"] == "candidate_supported"
    assert report["production_proven"] is True
    assert report["promotion_allowed"] is False
    assert report["requires_operator_review"] is True


def test_duplicate_game_result_is_idempotent() -> None:
    spec = definition()
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "experiments.json"
        for _ in range(2):
            payload = record_outcome(
                spec, agent_id="hellion", game_id="same-game", survived=True,
                placement=2, objective_progress=1, reward=10, path=path,
            )
    assert len(payload["outcomes"]) == 1
