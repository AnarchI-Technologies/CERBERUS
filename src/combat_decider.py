"""
Combat Cortex.
"""

from __future__ import annotations

from typing import Any

from agent_dossiers import AgentDossierStore
from cortex_types import CortexResult, action
from turn_state_model import AgentState, TurnState


WEAPON_BONUS = {
    "fist": (0, 0),
    "dagger": (16, 0),
    "sword": (20, 0),
    "katana": (35, 0),
    "bow": (5, 1),
    "pistol": (10, 1),
    "sniper": (28, 2),
}


def equipped_weapon(state: TurnState) -> tuple[str, int, int]:
    raw = state.self.raw.get("equippedWeapon") or {}
    name = str(raw.get("typeId") or raw.get("type") or raw.get("name") or "fist").lower()
    matched_name = ""
    fallback_bonus = 0
    fallback_range = 0
    for key, (known_bonus, known_range) in WEAPON_BONUS.items():
        if key in name:
            matched_name, fallback_bonus, fallback_range = key, known_bonus, known_range
            break
    explicit_bonus = raw.get("atkBonus")
    explicit_range = raw.get("range") if raw.get("range") is not None else raw.get("attackRange")
    try:
        bonus = int(explicit_bonus)
    except (TypeError, ValueError):
        bonus = None
    try:
        weapon_range = int(explicit_range)
    except (TypeError, ValueError):
        weapon_range = None
    if bonus is not None:
        return matched_name or name or "equipped", bonus, max(0, weapon_range if weapon_range is not None else fallback_range)
    if matched_name:
        return matched_name, fallback_bonus, fallback_range
    return "fist", 0, 0


def _target_distance(target: AgentState) -> int | None:
    for key in ("distance", "range", "rangeToTarget", "regionDistance", "distanceRegions"):
        value = target.raw.get(key)
        if isinstance(value, bool):
            continue
        try:
            if value not in (None, ""):
                return max(0, int(value))
        except (TypeError, ValueError):
            continue
    return None


def target_in_attack_range(state: TurnState, target: AgentState) -> bool:
    if target.raw.get("inRange") is True or target.raw.get("attackable") is True:
        return True
    if target.raw.get("inRange") is False or target.raw.get("attackable") is False:
        return False
    _weapon, _bonus, weapon_range = equipped_weapon(state)
    distance = _target_distance(target)
    if distance is not None:
        return distance <= weapon_range
    if target.region_id and state.current_region.id and target.region_id != state.current_region.id:
        return weapon_range > 0
    return True


def expected_damage(state: TurnState, target: AgentState) -> float:
    _, bonus, _ = equipped_weapon(state)
    return max(1.0, state.self.atk + bonus - target.defense * 0.5)


def target_score(state: TurnState, target: AgentState, dossiers: AgentDossierStore | None = None) -> float:
    damage = expected_damage(state, target)
    lethal = damage >= max(1, target.hp)
    hp_factor = max(0, 50 - target.hp)
    label = f"{target.name} {target.id}".lower()
    guardian_value = 25 if target.kind == "monster" and "guardian" in label else 0
    monster_value = 8 if target.kind == "monster" else 0

    score = damage + hp_factor + guardian_value + monster_value + (35 if lethal else 0)

    if dossiers and target.kind != "monster":
        record = dossiers.records.get(target.id)
        if record and any("moltz" in str(t).lower() for t in record.observed_tendencies):
            if target.hp and target.hp <= 40:
                score *= 1.45
            else:
                score *= 1.2

    return score


def is_worth_attacking(state: TurnState, target: AgentState) -> bool:
    damage = expected_damage(state, target)
    if damage <= 3 and target.hp > damage * 4:
        return False
    if target.atk >= state.self.hp and damage < target.hp:
        return False
    return True


class CombatCortex:
    name = "combat"

    def evaluate(self, state: TurnState, context: dict[str, Any]) -> list[CortexResult]:
        results: list[CortexResult] = []
        attack_cost = state.action_ep_cost("attack", 1)
        if not state.can_take_main_action or state.self.ep < attack_cost or state.alert_active or state.is_low_hp:
            return results

        # Load dossiers from context or disk to apply Prime Directive multipliers
        dossiers = context.get("dossiers") or context.get("dossier_store")
        if not dossiers:
            try:
                dossiers = AgentDossierStore().load()
            except Exception:
                dossiers = None

        targets = [
            agent
            for agent in state.visible_agents
            if agent.is_alive and agent.id != state.self.id and target_in_attack_range(state, agent)
        ]
        targets.extend(
            monster
            for monster in state.visible_monsters
            if monster.is_alive and target_in_attack_range(state, monster)
        )
        if not targets:
            return results

        targets = [target for target in targets if is_worth_attacking(state, target)]
        if not targets:
            return results

        target = sorted(targets, key=lambda item: target_score(state, item, dossiers), reverse=True)[0]
        score = target_score(state, target, dossiers)
        risk = max(5.0, target.atk * 0.7)
        target_type = "monster" if target.kind == "monster" else "agent"

        results.append(
            CortexResult(
                cortex=self.name,
                intent="take_favorable_attack",
                score=score,
                risk=risk,
                priority=62,
                action=action("attack", targetId=target.id, targetType=target_type),
                reason=f"favorable {target_type} target {target.name or target.id[:8]} hp={target.hp}",
                source_facts=["F|combat.damage", "F|combat.weapon", "F|action.cost"],
            )
        )
        return results
