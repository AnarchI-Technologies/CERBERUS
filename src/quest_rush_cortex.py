"""Preseason quest coverage without sacrificing Hellion's survival floor."""

from __future__ import annotations

import os

from combat_decider import is_worth_attacking, target_in_attack_range
from cortex_types import CortexResult, action, rest_action
from decision_engine import best_fallback_region
from turn_state_model import AgentState, TurnState


def quest_rush_enabled() -> bool:
    return os.getenv("CERBERUS_QUEST_RUSH_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def _guardian(target: AgentState) -> bool:
    return "guardian" in f"{target.id} {target.name} {target.raw.get('type', '')}".lower()


class QuestRushCortex:
    """Prioritize actions known to feed daily and season point tracks."""

    name = "quest_rush"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        if not quest_rush_enabled() or not state.can_take_main_action:
            return []
        if state.is_in_death_zone or state.is_pending_death_zone or state.alert_active or state.is_low_hp:
            return []

        results: list[CortexResult] = []
        region_label = f"{state.current_region.name} {state.current_region.terrain}".lower()
        ruin_available = "ruin" in region_label or bool(state.ruins)
        if ruin_available and state.self.ep >= 1 and not state.visible_agents:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_discover_ruin",
                    score=94,
                    risk=8 + state.alert_gauge * 2,
                    priority=73,
                    action=action("explore"),
                    reason="quest rush: discover/progress a ruin while the region is uncontested",
                    source_facts=["Q|daily_ruin", "Q|season.explore", "F|progression.ruin"],
                )
            )

        guardians = [target for target in state.visible_monsters if target.is_alive and _guardian(target)]
        guardians = [
            target for target in guardians
            if target_in_attack_range(state, target) and is_worth_attacking(state, target)
        ]
        if guardians and state.self.ep >= 1:
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
                    source_facts=["Q|daily_guardian", "Q|season.kills", "Q|season.damage"],
                )
            )

        # A top-ten placement is worth more than one blind movement. Preserve an
        # EP escape reserve once the field is nearly cut to the daily threshold.
        if 0 < state.alive_count <= 12 and state.self.ep <= 2 and not state.visible_agents:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="quest_top10_reserve",
                    score=88,
                    risk=2,
                    priority=74,
                    action=rest_action("quest rush: preserve EP reserve near top-ten cutoff"),
                    reason=f"quest rush: alive={state.alive_count}; bank survival and top-ten progress",
                    source_facts=["Q|daily_top10", "Q|season.top5", "Q|season.survival"],
                )
            )
        elif state.self.ep >= 3 and not state.visible_agents:
            target_region = best_fallback_region(state)
            if target_region:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="quest_exploration_rotation",
                        score=64,
                        risk=6,
                        priority=61,
                        action=action("move", regionId=target_region),
                        reason="quest rush: safe rotation builds exploration while keeping two EP in reserve",
                        source_facts=["Q|daily_explore", "Q|season.explore", "Q|season.survival"],
                    )
                )

        return results
