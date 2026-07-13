"""Compiled lesson pressure for deterministic gameplay.

This cortex turns retained memory into direct action pressure. It is the line
between "Hellion logged a fact" and "Hellion learned from it."
"""

from __future__ import annotations

from typing import Any

from agent_relations import alliance_value, betrayal_reward, is_allied, should_betray
from combat_decider import equipped_weapon, is_worth_attacking, target_in_attack_range
from cortex_types import CortexResult, action
from decision_engine import active_fallback_action
from free_action_abuse import best_ground_weapon, weapon_bonus_for_item
from hardened_strategy import load_hardened_strategy_rules
from memory_system import CompactMemoryStore
from runtime_state import match_evidence, suggested_edits
from turn_state_model import TurnState


def _lesson_texts(store: CompactMemoryStore | None) -> list[str]:
    if not store:
        return []
    lessons = store.data.get("lessons", [])
    if not isinstance(lessons, list):
        return []
    return [str(item).lower() for item in lessons]


def _dossier_records(context: dict[str, Any]) -> dict[str, Any]:
    store = context.get("dossier_store") or context.get("dossiers")
    records = getattr(store, "records", {}) if store is not None else {}
    return records if isinstance(records, dict) else {}


def _record_tendencies(record: Any) -> set[str]:
    tendencies = getattr(record, "observed_tendencies", []) if record is not None else []
    if not isinstance(tendencies, list):
        return set()
    return {str(item).lower() for item in tendencies if item}


def _has_tendency(tendencies: set[str], *markers: str) -> bool:
    return any(marker in tendency for marker in markers for tendency in tendencies)


def death_pressure(store: CompactMemoryStore | None) -> int:
    markers = ("failure:", "eliminated us", "killed_us", "agent_dead", "deathzone", "death zone")
    return sum(1 for lesson in _lesson_texts(store) if any(marker in lesson for marker in markers))


def current_game_blocked_targets(state: TurnState, *, limit: int = 120) -> set[str]:
    blocked: set[str] = set()
    for entry in match_evidence(limit=limit):
        if not isinstance(entry, dict):
            continue
        state_info = entry.get("state") if isinstance(entry.get("state"), dict) else {}
        if state.game_id and str(state_info.get("game_id") or "") not in {"", state.game_id}:
            continue
        action_info = entry.get("action") if isinstance(entry.get("action"), dict) else {}
        outcome = entry.get("outcome") if isinstance(entry.get("outcome"), dict) else {}
        text = f"{outcome.get('code','')} {outcome.get('message','')} {action_info.get('reason','')}".lower()
        target_id = str(action_info.get("targetId") or "")
        if target_id and any(marker in text for marker in ("target_blocked", "target blocked", "out-of-range", "out of range")):
            blocked.add(target_id)
    return blocked


def learned_failure_pressure() -> int:
    return sum(
        1
        for edit in suggested_edits(limit=80)
        if isinstance(edit, dict) and str(edit.get("status") or "open") == "open" and str(edit.get("priority") or "") in {"high", "critical"}
    )


def best_inventory_weapon_upgrade(state: TurnState) -> dict[str, Any] | None:
    _current_name, current_bonus, _current_range = equipped_weapon(state)
    best: dict[str, Any] | None = None
    best_bonus = current_bonus
    for item in state.inventory:
        bonus = weapon_bonus_for_item(item)
        if bonus > best_bonus and item.get("id"):
            best = item
            best_bonus = bonus
    return best


class LearnedPolicyCortex:
    name = "learned_policy"

    def evaluate(self, state: TurnState, context: dict[str, Any]) -> list[CortexResult]:
        store = context.get("memory_store") if isinstance(context.get("memory_store"), CompactMemoryStore) else None
        dossier_records = _dossier_records(context)
        results: list[CortexResult] = []
        applied: list[str] = []
        deaths = death_pressure(store)
        failure_pressure = learned_failure_pressure()
        rules = load_hardened_strategy_rules().get("rules", {})
        heal_floor = float(rules.get("heal_hp_ratio_floor", 0.68) or 0.68)
        known_killer_floor = float(rules.get("known_killer_hp_ratio_floor", 0.72) or 0.72)
        finisher_floor = float(rules.get("observed_finisher_hp_ratio_floor", 0.70) or 0.70)
        repeat_prey_min_kills = int(rules.get("repeat_prey_min_kills", 1) or 1)

        heal_item = state.best_heal_item()
        hp_ratio = state.self.hp / max(1, state.self.max_hp)
        can_press_attack = (
            state.can_take_main_action
            and not state.alert_active
            and not state.is_low_hp
            and not state.is_in_death_zone
            and not state.is_pending_death_zone
        )
        if state.can_take_main_action and heal_item and deaths and hp_ratio <= heal_floor:
            applied.append("earlier_heal_after_death_lessons")
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="apply_retained_survival_lesson",
                    score=91 + min(8, deaths),
                    risk=4,
                    priority=94,
                    veto=True,
                    action=action("use_item", itemId=heal_item.get("id")),
                    reason=f"retained lesson: prior deaths justify early heal at {state.self.hp}/{state.self.max_hp}",
                    source_facts=["L|survival.failure", "F|items.recovery"],
                )
                    )

        if state.can_take_main_action and state.self.ep >= state.action_ep_cost("attack", 1):
            for agent in state.visible_agents:
                if not agent.is_alive or agent.id == state.self.id:
                    continue
                record = dossier_records.get(agent.id)
                if record is None:
                    continue
                killed_us = int(getattr(record, "killed_us", 0) or 0)
                killed_by_us = int(getattr(record, "killed_by_us", 0) or 0)
                tendencies = _record_tendencies(record)
                in_range = target_in_attack_range(state, agent)
                allied = is_allied(record)
                alliance_points = alliance_value(record)
                betrayal_points = betrayal_reward(record, state, agent)

                if allied and in_range and not should_betray(record, state, agent):
                    fallback = active_fallback_action(state)
                    applied.append("preserve_useful_alliance")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="preserve_provisional_alliance",
                            score=92 + min(4, alliance_points // 3),
                            risk=3,
                            priority=96,
                            veto=True,
                            action=fallback,
                            reason=f"retained lesson: {agent.name or agent.id[:8]} has been useful enough to preserve the alliance while its value beats betrayal",
                            source_facts=["D|social.dossiers", "L|alliances.useful_contact"],
                        )
                    )
                    continue

                if allied and in_range and should_betray(record, state, agent) and can_press_attack and is_worth_attacking(state, agent):
                    applied.append("silent_betrayal_more_profitable")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="betray_when_reward_outweighs_alliance",
                            score=95 + min(4, betrayal_points),
                            risk=max(4, agent.atk * 0.55),
                            priority=97,
                            veto=True,
                            action=action("attack", targetId=agent.id, targetType="agent"),
                            reason=f"retained lesson: alliance with {agent.name or agent.id[:8]} is now worth less than the tactical betrayal payoff; strike cleanly and stay silent",
                            source_facts=["D|social.dossiers", "L|alliances.betrayal_math", "F|combat.range"],
                        )
                    )
                    continue

                if killed_us > killed_by_us and hp_ratio <= known_killer_floor:
                    if heal_item:
                        applied.append("heal_before_known_killer")
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="apply_known_killer_survival_lesson",
                                score=93 + min(4, killed_us),
                                risk=4,
                                priority=95,
                                veto=True,
                                action=action("use_item", itemId=heal_item.get("id")),
                                reason=f"retained lesson: {agent.name or agent.id[:8]} has finished us before; heal before re-engaging",
                                source_facts=["D|social.dossiers", "L|survival.repeat_killer"],
                            )
                        )
                    elif in_range:
                        fallback = active_fallback_action(state)
                        applied.append("avoid_known_killer_when_weak")
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="apply_known_killer_escape_lesson",
                                score=85 + min(4, killed_us),
                                risk=9,
                                priority=87,
                                action=fallback,
                                reason=f"retained lesson: {agent.name or agent.id[:8]} has finished us before; disengage until the board improves",
                                source_facts=["D|social.dossiers", "L|survival.repeat_killer"],
                            )
                        )

                if "finishes_low_targets" in tendencies and hp_ratio <= finisher_floor:
                    if heal_item:
                        applied.append("heal_before_observed_finisher")
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="apply_observed_finisher_survival_lesson",
                                score=89,
                                risk=4,
                                priority=92,
                                veto=True,
                                action=action("use_item", itemId=heal_item.get("id")),
                                reason=f"retained lesson: {agent.name or agent.id[:8]} finishes weak targets; heal before becoming the next one",
                                source_facts=["D|social.dossiers", "L|opponents.finisher_pattern"],
                            )
                        )
                    elif in_range:
                        fallback = active_fallback_action(state)
                        applied.append("avoid_observed_finisher_when_weak")
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="apply_observed_finisher_escape_lesson",
                                score=82,
                                risk=8,
                                priority=84,
                                action=fallback,
                                reason=f"retained lesson: {agent.name or agent.id[:8]} closes on weak targets; disengage until the board improves",
                                source_facts=["D|social.dossiers", "L|opponents.finisher_pattern"],
                            )
                        )

                if (
                    killed_by_us >= repeat_prey_min_kills
                    and killed_us == 0
                    and in_range
                    and can_press_attack
                    and is_worth_attacking(state, agent)
                ):
                    applied.append("press_repeat_prey")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_repeat_prey_lesson",
                            score=87 + min(4, killed_by_us),
                            risk=max(4, agent.atk * 0.55),
                            priority=86,
                            action=action("attack", targetId=agent.id, targetType="agent"),
                            reason=f"retained lesson: {agent.name or agent.id[:8]} has folded to this pressure before; press the advantage",
                            source_facts=["D|social.dossiers", "L|combat.repeat_prey", "F|combat.range"],
                        )
                    )

                if (
                    "dies_under_pressure" in tendencies
                    and in_range
                    and can_press_attack
                    and is_worth_attacking(state, agent)
                ):
                    applied.append("press_observed_fragile_target")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_observed_fragile_target_lesson",
                            score=84,
                            risk=max(4, agent.atk * 0.5),
                            priority=83,
                            action=action("attack", targetId=agent.id, targetType="agent"),
                            reason=f"retained lesson: {agent.name or agent.id[:8]} folds under pressure; take the favorable fight",
                            source_facts=["D|social.dossiers", "L|opponents.fragile_pattern", "F|combat.range"],
                        )
                    )

                if (
                    in_range
                    and can_press_attack
                    and is_worth_attacking(state, agent)
                    and _has_tendency(tendencies, "collects_smoltz", "collects_high_value_loot", "collects_loadout")
                ):
                    applied.append("intercept_known_loot_carrier")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_known_loot_carrier_lesson",
                            score=86 if _has_tendency(tendencies, "collects_smoltz") else 83,
                            risk=max(5, agent.atk * 0.55),
                            priority=85,
                            action=action("attack", targetId=agent.id, targetType="agent"),
                            reason=f"retained lesson: {agent.name or agent.id[:8]} tends to leave fights with value; intercept before they cash out",
                            source_facts=["D|social.dossiers", "L|economy.loot_carrier", "F|combat.range"],
                        )
                    )

        blocked = current_game_blocked_targets(state)
        if blocked and state.can_take_main_action:
            visible_blocked = any(target.id in blocked for target in [*state.visible_agents, *state.visible_monsters])
            if visible_blocked:
                inventory_weapon = best_inventory_weapon_upgrade(state)
                ground_weapon = best_ground_weapon(state, equipped_weapon(state)[1])
                if inventory_weapon:
                    applied.append("equip_before_retrying_blocked_target")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_blocked_target_weapon_lesson",
                            score=88,
                            risk=2,
                            priority=89,
                            action=action("equip", itemId=inventory_weapon.get("id")),
                            reason="retained lesson: blocked target seen; equip stronger weapon before retrying combat",
                            source_facts=["L|runtime.target_blocked", "F|combat.weapon"],
                        )
                    )
                elif ground_weapon:
                    applied.append("pickup_before_retrying_blocked_target")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_blocked_target_weapon_lesson",
                            score=86,
                            risk=3,
                            priority=88,
                            action=action("pickup", itemId=ground_weapon.get("id")),
                            reason="retained lesson: blocked target seen; pick up weapon upgrade before retrying combat",
                            source_facts=["L|runtime.target_blocked", "F|action.free"],
                        )
                    )
                else:
                    fallback = active_fallback_action(state)
                    applied.append("reposition_after_blocked_target")
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="apply_blocked_target_reposition_lesson",
                            score=62,
                            risk=8,
                            priority=67,
                            action=fallback,
                            reason="retained lesson: avoid repeating blocked target; reposition or scout",
                            source_facts=["L|runtime.target_blocked"],
                        )
                    )

        if applied or failure_pressure:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="compiled_lessons_available",
                    score=8 + failure_pressure,
                    risk=0,
                    priority=6,
                    action=None,
                    reason="retained runtime lessons compiled into deterministic policy pressure",
                    side_effects=[
                        {
                            "type": "retained_lessons_compiled",
                            "applied": applied,
                            "death_pressure": deaths,
                            "open_high_priority_suggestions": failure_pressure,
                        }
                    ],
                    source_facts=["L|compiled_policy"],
                )
            )

        return results
