"""Preseason quest coverage without sacrificing Hellion's survival floor."""

from __future__ import annotations

import math
import os

from combat_decider import expected_damage, is_worth_attacking, target_in_attack_range
from cortex_types import CortexResult, action, rest_action
from decision_engine import best_fallback_region
from free_action_abuse import weapon_bonus_for_item
from turn_state_model import AgentState, TurnState


MAX_RUIN_ALERT_GAUGE = 6
MAX_GUARDIAN_TURNS_TO_KILL = 3
MIN_GUARDIAN_EXIT_HP_RATIO = 0.55
GUARDIAN_EXIT_EP_RESERVE = 1
MAX_RIVAL_TURNS_TO_KILL = 1
MIN_RIVAL_EXIT_HP_RATIO = 0.65
RIVAL_EXIT_EP_RESERVE = 1
QUEST_PICKUP_RESERVED_SLOTS = 2
MATCH_RELIC_CAP = 5


def quest_rush_enabled() -> bool:
    return os.getenv("CERBERUS_QUEST_RUSH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _guardian(target: AgentState) -> bool:
    return "guardian" in f"{target.id} {target.name} {target.raw.get('type', '')}".lower()


def _guardian_engagement_is_safe(state: TurnState, target: AgentState) -> bool:
    damage = max(1.0, expected_damage(state, target))
    turns_to_kill = max(1, math.ceil(max(1, target.hp) / damage))
    spendable_ep = max(0, state.self.ep - GUARDIAN_EXIT_EP_RESERVE)
    attack_cost = max(1, state.action_ep_cost("attack", 1))
    affordable_attacks = spendable_ep // attack_cost
    if turns_to_kill > MAX_GUARDIAN_TURNS_TO_KILL or turns_to_kill > affordable_attacks:
        return False
    incoming = max(1.0, target.atk - state.self.defense * 0.5)
    projected_hp = state.self.hp - incoming * turns_to_kill
    return projected_hp >= state.self.max_hp * MIN_GUARDIAN_EXIT_HP_RATIO


def _rival_engagement_is_safe(state: TurnState, target: AgentState) -> bool:
    """Allow only short, favorable fights that retain an escape reserve."""
    damage = max(1.0, expected_damage(state, target))
    turns_to_kill = max(1, math.ceil(max(1, target.hp) / damage))
    attack_cost = max(1, state.action_ep_cost("attack", 1))
    affordable_attacks = max(0, state.self.ep - RIVAL_EXIT_EP_RESERVE) // attack_cost
    if turns_to_kill > MAX_RIVAL_TURNS_TO_KILL or turns_to_kill > affordable_attacks:
        return False
    incoming = max(1.0, target.atk - state.self.defense * 0.5)
    projected_hp = state.self.hp - incoming * turns_to_kill
    return projected_hp >= state.self.max_hp * MIN_RIVAL_EXIT_HP_RATIO


def _ruin_progressable(state: TurnState) -> bool:
    if state.alert_gauge > MAX_RUIN_ALERT_GAUGE:
        return False
    if not state.ruins:
        return True
    for ruin in state.ruins.values():
        if ruin.is_empty:
            continue
        if ruin.occupied_by and ruin.occupied_by not in {state.self.id, state.agent_id}:
            continue
        if ruin.max_gauge > 0 and ruin.gauge >= ruin.max_gauge:
            continue
        return True
    return False


def _generic_quest_pickup(state: TurnState) -> dict | None:
    if len(state.inventory) >= max(0, 10 - QUEST_PICKUP_RESERVED_SLOTS):
        return None
    for item in state.local_ground_items():
        if not item.get("id") or weapon_bonus_for_item(item) > 0:
            continue
        return item
    return None


def _carried_relic_count(state: TurnState) -> int:
    return sum(
        1 for item in state.inventory
        if "relic" in str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
    )


def _known_relic_ruin(state: TurnState) -> bool:
    return any("relic" in ruin.content_type.lower() for ruin in state.ruins.values() if not ruin.is_empty)


def _combat_active(state: TurnState) -> bool:
    targets = (*state.visible_agents, *state.visible_monsters)
    return any(
        target.is_alive
        and target.id not in {state.self.id, state.agent_id}
        and target_in_attack_range(state, target)
        for target in targets
    )


class QuestRushCortex:
    """Prioritize actions known to feed daily and season point tracks."""

    name = "quest_rush"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        if not quest_rush_enabled():
            return []
        if state.is_in_death_zone or state.is_pending_death_zone or state.alert_active or state.is_low_hp:
            return []

        results: list[CortexResult] = []
        pickup = _generic_quest_pickup(state)
        if pickup and state.objective_needs_progress("items") and not state.visible_agents:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_item_pickup",
                    score=56,
                    risk=1,
                    priority=79,
                    action=action("pickup", itemId=pickup.get("id")),
                    reason="quest rush: collect safe local item progress while preserving two inventory slots",
                    source_facts=["Q|season.items", "F|action.free", "F|inventory.reserve"],
                )
            )

        if not state.can_take_main_action:
            return results

        region_label = f"{state.current_region.name} {state.current_region.terrain}".lower()
        known_relic_ruin = _known_relic_ruin(state)
        explore_needed = state.objective_needs_progress("explore")
        relic_capacity_available = _carried_relic_count(state) < MATCH_RELIC_CAP
        ruin_available = (
            ("ruin" in region_label or bool(state.ruins))
            and _ruin_progressable(state)
            and (not known_relic_ruin or relic_capacity_available)
            and (known_relic_ruin or explore_needed)
        )
        explore_cost = state.action_ep_cost("explore", 1)
        if ruin_available and state.self.ep >= explore_cost and not _combat_active(state):
            relic_sprint = known_relic_ruin and state.self.ep >= explore_cost + 1
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_relic_acquisition" if relic_sprint else "quest_discover_ruin",
                    score=98 if relic_sprint else 94,
                    risk=8 + state.alert_gauge * 2,
                    priority=76 if relic_sprint else 75,
                    action=action("explore"),
                    reason=(
                        "level-five sprint: acquire a known relic with bag and EP reserves"
                        if relic_sprint
                        else "quest rush: discover/progress a ruin while the region is uncontested"
                    ),
                    source_facts=["Q|daily_ruin", "Q|season.explore", "Q|season.items", "F|progression.ruin"],
                )
            )

        guardians = [target for target in state.visible_monsters if target.is_alive and _guardian(target)]
        guardians = [
            target for target in guardians
            if target_in_attack_range(state, target)
            and is_worth_attacking(state, target)
            and _guardian_engagement_is_safe(state, target)
        ]
        attack_cost = state.action_ep_cost("attack", 1)
        if guardians and state.self.ep >= attack_cost and state.objective_needs_progress("damage"):
            target = sorted(guardians, key=lambda item: (item.hp, item.atk, item.id))[0]
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_guardian_kill",
                    score=100 - min(60, target.hp / 2),
                    risk=max(5, target.atk * 0.75),
                    priority=72,
                    action=action("attack", targetId=target.id, targetType="monster"),
                    reason=f"quest rush: safe guardian progress; target hp={target.hp}",
                    source_facts=["Q|daily_guardian", "Q|season.damage"],
                )
            )

        rivals = [
            target for target in state.visible_agents
            if target.is_alive
            and target.id not in {state.self.id, state.agent_id}
            and target_in_attack_range(state, target)
            and is_worth_attacking(state, target)
            and _rival_engagement_is_safe(state, target)
        ]
        if rivals and state.self.ep >= attack_cost and (
            state.objective_needs_progress("kills") or state.objective_needs_progress("damage")
        ):
            target = sorted(rivals, key=lambda item: (item.hp, item.atk, item.id))[0]
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_safe_rival_hunt",
                    score=102 - min(60, target.hp / 2),
                    risk=max(6, target.atk * 0.8),
                    priority=76,
                    action=action("attack", targetId=target.id, targetType="agent"),
                    reason=f"level-five sprint: favorable rival fight; target hp={target.hp}",
                    source_facts=["Q|daily_first_blood", "Q|season.rival_kills", "Q|season.damage"],
                )
            )

        # A top-ten placement is worth more than one blind movement. Preserve an
        # EP escape reserve once the field is nearly cut to the daily threshold.
        if (
            0 < state.alive_count <= 10
            and not state.visible_agents
            and (state.objective_needs_progress("top5") or state.objective_needs_progress("survival"))
        ):
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_top10_reserve",
                    score=88,
                    risk=2,
                    priority=78,
                    action=rest_action("quest rush: preserve EP reserve near top-ten cutoff"),
                    reason=f"quest rush: alive={state.alive_count}; bank survival and top-ten progress",
                    source_facts=["Q|daily_top10", "Q|season.top5", "Q|season.survival"],
                )
            )
        elif explore_needed and state.self.ep >= 3 and not state.visible_agents:
            target_region = best_fallback_region(state)
            if target_region:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="quest_exploration_rotation",
                        score=92,
                        risk=6,
                        priority=75,
                        action=action("move", regionId=target_region),
                        reason="level-five sprint: safe rotation targets lagging exploration while keeping two EP in reserve",
                        source_facts=["Q|daily_explore", "Q|season.explore", "Q|season.survival"],
                    )
                )

        return results
