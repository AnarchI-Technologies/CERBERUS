"""Deterministic A/B evidence for isolated CERBERUS game agents."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean
from typing import Any

from memory_system import atomic_write_text
from runtime_state import memory_dir


@dataclass(frozen=True, slots=True)
class ExperimentDefinition:
    experiment_id: str
    control_agent_id: str
    candidate_agent_id: str
    control_variant: str
    candidate_variant: str
    minimum_games_per_variant: int = 20
    maximum_games_per_variant: int = 100
    minimum_survival_ratio: float = 0.9

    def __post_init__(self) -> None:
        if not self.experiment_id.strip():
            raise ValueError("experiment_id is required")
        if not self.control_agent_id.strip() or not self.candidate_agent_id.strip():
            raise ValueError("both isolated agent IDs are required")
        if self.control_agent_id == self.candidate_agent_id:
            raise ValueError("control and candidate agents must be distinct")
        if not 1 <= self.minimum_games_per_variant <= self.maximum_games_per_variant <= 500:
            raise ValueError("invalid bounded game counts")


def assignment(definition: ExperimentDefinition, agent_id: str) -> str:
    if agent_id == definition.control_agent_id:
        return definition.control_variant
    if agent_id == definition.candidate_agent_id:
        return definition.candidate_variant
    raise ValueError("agent is outside this experiment")


def _store_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else memory_dir() / "strategy_experiments.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def record_outcome(
    definition: ExperimentDefinition,
    *,
    agent_id: str,
    game_id: str,
    survived: bool,
    placement: int,
    objective_progress: float,
    reward: float,
    paid_entry_cost: float = 0.0,
    path: str | Path | None = None,
) -> dict[str, Any]:
    variant = assignment(definition, agent_id)
    target = _store_path(path)
    payload = _load(target)
    rows = payload.get("outcomes") if isinstance(payload.get("outcomes"), list) else []
    if any(str(item.get("game_id")) == game_id and str(item.get("agent_id")) == agent_id for item in rows if isinstance(item, dict)):
        return payload
    variant_count = sum(1 for item in rows if isinstance(item, dict) and item.get("variant") == variant)
    if variant_count >= definition.maximum_games_per_variant:
        return payload
    rows.append(
        {
            "agent_id": agent_id,
            "game_id": str(game_id)[:100],
            "variant": variant,
            "survived": bool(survived),
            "placement": max(1, int(placement)),
            "objective_progress": float(objective_progress),
            "reward": float(reward),
            "paid_entry_cost": max(0.0, float(paid_entry_cost)),
        }
    )
    payload = {"schema_version": 1, "definition": asdict(definition), "outcomes": rows}
    atomic_write_text(target, json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True))
    return payload


def evaluate(definition: ExperimentDefinition, *, path: str | Path | None = None) -> dict[str, Any]:
    rows = _load(_store_path(path)).get("outcomes", [])
    if not isinstance(rows, list):
        rows = []
    control = [item for item in rows if isinstance(item, dict) and item.get("variant") == definition.control_variant]
    candidate = [item for item in rows if isinstance(item, dict) and item.get("variant") == definition.candidate_variant]

    def metrics(items: list[dict[str, Any]]) -> dict[str, float | int]:
        if not items:
            return {"games": 0, "survival_rate": 0.0, "mean_placement": 0.0, "objective_per_game": 0.0, "net_reward": 0.0}
        return {
            "games": len(items),
            "survival_rate": mean(1.0 if item.get("survived") else 0.0 for item in items),
            "mean_placement": mean(float(item.get("placement") or 0) for item in items),
            "objective_per_game": mean(float(item.get("objective_progress") or 0) for item in items),
            "net_reward": sum(float(item.get("reward") or 0) - float(item.get("paid_entry_cost") or 0) for item in items),
        }

    control_metrics = metrics(control)
    candidate_metrics = metrics(candidate)
    enough = min(int(control_metrics["games"]), int(candidate_metrics["games"])) >= definition.minimum_games_per_variant
    survival_guard = (
        float(candidate_metrics["survival_rate"])
        >= float(control_metrics["survival_rate"]) * definition.minimum_survival_ratio
    )
    objective_better = float(candidate_metrics["objective_per_game"]) > float(control_metrics["objective_per_game"])
    supported = enough and survival_guard and objective_better
    return {
        "experiment_id": definition.experiment_id,
        "control": control_metrics,
        "candidate": candidate_metrics,
        "recommendation": "candidate_supported" if supported else ("retain_control" if enough else "insufficient_evidence"),
        "production_proven": supported,
        "promotion_allowed": False,
        "requires_operator_review": True,
    }
