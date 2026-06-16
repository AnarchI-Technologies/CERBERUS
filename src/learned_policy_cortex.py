"""Compiled lesson pressure for deterministic gameplay.

This cortex turns retained memory into direct action pressure. It is the line
between "Hellion logged a fact" and "Hellion learned from it."
"""

from __future__ import annotations

from typing import Any

from combat_decider import equipped_weapon
from cortex_types import CortexResult, action
from decision_engine import active_fallback_action
from free_action_abuse import best_ground_weapon, weapon_bonus_for_item
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
        results: list[CortexResult] = []
        applied: list[str] = []
        deaths = death_pressure(store)
        failure_pressure = learned_failure_pressure()

        heal_item = state.best_heal_item()
        hp_ratio = state.self.hp / max(1, state.self.max_hp)
        if state.can_take_main_action and heal_item and deaths and hp_ratio <= 0.68:
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
