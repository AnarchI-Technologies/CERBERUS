"""
Multicortex arbiter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cortex_types import Cortex, CortexResult, action, rest_action
from knowledge_base import KnowledgeBase
from memory_system import CompactMemoryStore
from turn_state_model import AgentState, TurnState


def has_usable_turn_facts(state: TurnState) -> bool:
    return bool(
        state.self.id
        or state.current_region.id
        or state.connected_regions
        or state.visible_regions
        or state.visible_agents
        or state.visible_monsters
        or state.visible_items
        or state.current_region.items
        or state.inventory
    )


@dataclass(slots=True)
class ArbiterDecision:
    action: dict[str, Any]
    reason: str
    winner: CortexResult | None = None
    candidates: list[dict[str, Any]] = field(default_factory=list)
    side_effects: list[dict[str, Any]] = field(default_factory=list)

    def to_action(self) -> dict[str, Any]:
        out = dict(self.action)
        if self.reason and "reason" not in out:
            out["reason"] = self.reason[:700]
        if self.side_effects:
            out["_side_effects"] = self.side_effects
        return out


class Arbiter:
    def choose(self, results: list[CortexResult], state: TurnState) -> ArbiterDecision:
        side_effects = [
            effect
            for result in results
            for effect in result.side_effects
        ]
        candidates = [result for result in results if result.action]
        vetoes = [result for result in candidates if result.veto]
        pool = vetoes or candidates

        if not pool:
            fallback = active_fallback_action(state)
            return ArbiterDecision(
                action=fallback,
                reason=str(fallback.get("reason") or "no cortex produced an action"),
                candidates=[result.to_plan_entry() for result in results],
                side_effects=side_effects,
            )

        winner = sorted(
            pool,
            key=lambda result: (result.priority, result.utility, result.score),
            reverse=True,
        )[0]
        return ArbiterDecision(
            action=winner.action or rest_action(),
            reason=winner.reason or winner.intent,
            winner=winner,
            candidates=[result.to_plan_entry() for result in results],
            side_effects=side_effects,
        )


def active_fallback_action(state: TurnState) -> dict[str, Any]:
    if not has_usable_turn_facts(state):
        return rest_action("waiting for usable live turn facts")
    if not state.can_take_main_action:
        return rest_action("waiting for main-action cooldown")
    if state.self.ep <= 0:
        return rest_action("EP empty; recover before movement")
    best_region = best_fallback_region(state)
    if best_region:
        return action("move", regionId=best_region, reason="scout fallback; move to best scored safe region")
    if state.current_region.terrain.lower() == "ruin" or "ruin" in state.current_region.name.lower():
        return action("explore", reason="scout fallback; ruin present and no higher-priority action")
    for region in state.connected_safe_regions():
        region_id = str(region.get("id") or region.get("regionId") or "")
        if region_id and region_id != state.current_region.id:
            return action("move", regionId=region_id, reason="scout fallback; move to visible region")
    return action("explore", reason="scout fallback; reveal map options")


def _region_id(region: dict | str) -> str:
    return str(region.get("id") or region.get("regionId") or "") if isinstance(region, dict) else str(region or "")


def _region_label(region: dict | str) -> str:
    if not isinstance(region, dict):
        return ""
    return f"{region.get('name','')} {region.get('terrain','')} {region.get('type','')}".lower()


def _item_label(item: dict) -> str:
    return str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()


def _target_region_score(state: TurnState, region_id: str) -> float:
    score = 0.0
    for target in [*state.visible_agents, *state.visible_monsters]:
        if not isinstance(target, AgentState) or not target.is_alive or target.id == state.self.id:
            continue
        if target.region_id == region_id:
            score += 24 if target.hp <= 35 else 10
            if target.kind == "monster" and "guardian" in f"{target.name} {target.id}".lower():
                score += 12
    return score


def fallback_region_score(state: TurnState, region: dict | str) -> float:
    region_id = _region_id(region)
    if not region_id:
        return -999.0
    label = _region_label(region)
    score = 0.0
    if "water" in label or "storm" in label:
        score -= 12
    if "ruin" in label:
        score += 8
    if "hill" in label or "plains" in label:
        score += 3
    if isinstance(region, dict):
        score += min(6, len(region.get("connections", []) or []))
        for item in region.get("items", []) or []:
            item_label = _item_label(item) if isinstance(item, dict) else ""
            if any(term in item_label for term in ("smoltz", "moltz", "relic", "pack")):
                score += 20
            elif any(term in item_label for term in ("katana", "sniper", "sword", "pistol", "dagger")):
                score += 12
            elif any(term in item_label for term in ("medkit", "bandage", "energy")):
                score += 7
    score += _target_region_score(state, region_id)
    return score


def best_fallback_region(state: TurnState) -> str:
    safe = state.connected_safe_regions()
    if not safe:
        return ""
    scored = [(fallback_region_score(state, region), _region_id(region)) for region in safe]
    scored = [(score, region_id) for score, region_id in scored if region_id]
    if not scored:
        return ""
    return sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[0][1]


def make_plan(
    *,
    state: dict[str, Any] | TurnState,
    threats: Any = None,
    opportunities: Any = None,
    memory: str = "",
    memory_store: CompactMemoryStore | None = None,
    dossier_store: Any = None,
    owner_messages: list[dict[str, Any]] | None = None,
    knowledge: KnowledgeBase | None = None,
    cortexes: list[Cortex] | None = None,
    strategy_registry: Any = None,
) -> dict[str, Any]:
    turn_state = state if isinstance(state, TurnState) else TurnState.from_snapshot(state)
    kb = knowledge or KnowledgeBase().load()
    store = memory_store or CompactMemoryStore().load()
    context = {
        "threats": threats or [],
        "opportunities": opportunities or [],
        "memory": memory or store.agent_context(),
        "memory_store": store,
        "dossier_store": dossier_store,
        "dossiers": dossier_store,
        "owner_messages": owner_messages or [],
        "knowledge": kb,
    }
    if cortexes and strategy_registry is not None:
        raise ValueError("provide cortexes or strategy_registry, not both")

    registry_metrics = None
    results: list[CortexResult] = []
    if strategy_registry is not None:
        evaluation = strategy_registry.evaluate_all(turn_state, context)
        results.extend(evaluation.results)
        registry_metrics = {
            "providers_evaluated": list(evaluation.providers_evaluated),
            "strategies_eligible": evaluation.strategies_eligible,
            "strategies_skipped": evaluation.strategies_skipped,
        }
    else:
        for cortex in cortexes or []:
            results.extend(cortex.evaluate(turn_state, context))
    decision = Arbiter().choose(results, turn_state)
    return {
        "action": decision.to_action(),
        "reason": decision.reason,
        "winner": decision.winner.to_plan_entry() if decision.winner else None,
        "candidates": decision.candidates,
        "side_effects": decision.side_effects,
        "strategy_registry": registry_metrics,
    }
