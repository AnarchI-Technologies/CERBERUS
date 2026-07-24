"""Shadow parity for the hardcoded cortex chain and callable strategy registry."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from combat_decider import CombatCortex
from decision_engine import Arbiter
from ep_economy_engine import EconomyCortex
from free_action_abuse import FreeActionCortex
from learned_policy_cortex import LearnedPolicyCortex
from memory_cortex import MemoryCortex
from owner_command_cortex import OwnerCommandCortex
from progression_cortex import ProgressionCortex
from quest_rush_cortex import QuestRushCortex
from social_cortex import SocialCortex
from threat_engine import ThreatCortex
from turn_state_model import TurnState
from utility_cortex import UtilityCortex

from .legacy_strategy_registry import LegacyStrategyRegistry, RegistryEvaluation


LEGACY_PROVIDER_ORDER = (
    "threat_engine",
    "owner_command_cortex",
    "learned_policy_cortex",
    "free_action_abuse",
    "utility_cortex",
    "progression_cortex",
    "quest_rush_cortex",
    "combat_decider",
    "ep_economy_engine",
    "social_cortex",
    "memory_cortex",
)


def build_legacy_strategy_providers(
    *,
    memory_store: Any = None,
    dossier_store: Any = None,
) -> dict[str, Any]:
    """Build the current production providers in their exact arbitration order."""

    return {
        "threat_engine": ThreatCortex(),
        "owner_command_cortex": OwnerCommandCortex(),
        "learned_policy_cortex": LearnedPolicyCortex(),
        "free_action_abuse": FreeActionCortex(),
        "utility_cortex": UtilityCortex(),
        "progression_cortex": ProgressionCortex(),
        "quest_rush_cortex": QuestRushCortex(),
        "combat_decider": CombatCortex(),
        "ep_economy_engine": EconomyCortex(),
        "social_cortex": SocialCortex(dossier_store=dossier_store),
        "memory_cortex": MemoryCortex(
            memory_store=memory_store,
            dossier_store=dossier_store,
        ),
    }


def _candidate_entries(results: list[Any] | tuple[Any, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(result.to_plan_entry() for result in results)


def _winner_entry(decision: Any) -> dict[str, Any] | None:
    return decision.winner.to_plan_entry() if decision.winner else None


@dataclass(frozen=True, slots=True)
class StrategyParityReport:
    passed: bool
    mismatches: tuple[str, ...]
    baseline_candidates: tuple[dict[str, Any], ...]
    registry_candidates: tuple[dict[str, Any], ...]
    baseline_action: dict[str, Any]
    registry_action: dict[str, Any]
    baseline_winner: dict[str, Any] | None
    registry_winner: dict[str, Any] | None
    providers_evaluated: tuple[str, ...]
    strategies_eligible: int
    strategies_skipped: int


def compare_legacy_strategy_registry(
    state: TurnState,
    context: dict[str, Any],
    *,
    baseline_providers: Mapping[str, Any],
    registry_providers: Mapping[str, Any],
) -> StrategyParityReport:
    """Evaluate both paths independently and report exact behavioral parity."""

    if tuple(baseline_providers) != LEGACY_PROVIDER_ORDER:
        raise ValueError("baseline providers are not in canonical legacy order")
    if tuple(registry_providers) != LEGACY_PROVIDER_ORDER:
        raise ValueError("registry providers are not in canonical legacy order")

    baseline_results: list[Any] = []
    for provider in baseline_providers.values():
        baseline_results.extend(provider.evaluate(state, context))

    registry_evaluation: RegistryEvaluation = LegacyStrategyRegistry(
        dict(registry_providers)
    ).evaluate_all(state, context)
    registry_results = list(registry_evaluation.results)

    baseline_decision = Arbiter().choose(baseline_results, state)
    registry_decision = Arbiter().choose(registry_results, state)
    baseline_candidates = _candidate_entries(baseline_results)
    registry_candidates = _candidate_entries(registry_results)
    baseline_action = baseline_decision.to_action()
    registry_action = registry_decision.to_action()
    baseline_winner = _winner_entry(baseline_decision)
    registry_winner = _winner_entry(registry_decision)

    comparisons = {
        "candidates": baseline_candidates == registry_candidates,
        "action": baseline_action == registry_action,
        "winner": baseline_winner == registry_winner,
        "side_effects": baseline_decision.side_effects
        == registry_decision.side_effects,
        "reason": baseline_decision.reason == registry_decision.reason,
    }
    mismatches = tuple(name for name, passed in comparisons.items() if not passed)
    return StrategyParityReport(
        passed=not mismatches,
        mismatches=mismatches,
        baseline_candidates=baseline_candidates,
        registry_candidates=registry_candidates,
        baseline_action=baseline_action,
        registry_action=registry_action,
        baseline_winner=baseline_winner,
        registry_winner=registry_winner,
        providers_evaluated=registry_evaluation.providers_evaluated,
        strategies_eligible=registry_evaluation.strategies_eligible,
        strategies_skipped=registry_evaluation.strategies_skipped,
    )
