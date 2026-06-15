"""
Combat Cortex.
"""

from __future__ import annotations

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
    for key, (bonus, weapon_range) in WEAPON_BONUS.items():
        if key in name:
            return key, bonus, weapon_range
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
    _weapon, _bonus, weapon_range = equipped_weapon(state)
    distance = _target_distance(target)
    if distance is not None:
        return distance <= weapon_range
    if target.region_id and state.current_region.id and target.region_id != state.current_region.id:
        return weapon_range > 0
    if target.raw.get("inRange") is False or target.raw.get("attackable") is False:
        return False
    return True


def expected_damage(state: TurnState, target: AgentState) -> float:
    _, bonus, _ = equipped_weapon(state)
    return max(1.0, state.self.atk + bonus - target.defense * 0.5)


def target_score(state: TurnState, target: AgentState) -> float:
    damage = expected_damage(state, target)
    lethal = damage >= max(1, target.hp)
    hp_factor = max(0, 50 - target.hp)
    label = f"{target.name} {target.id}".lower()
    guardian_value = 25 if target.kind == "monster" and "guardian" in label else 0
    monster_value = 8 if target.kind == "monster" else 0
    return damage + hp_factor + guardian_value + monster_value + (35 if lethal else 0)


def is_worth_attacking(state: TurnState, target: AgentState) -> bool:
    damage = expected_damage(state, target)
    if damage <= 3 and target.hp > damage * 4:
        return False
    if target.atk >= state.self.hp and damage < target.hp:
        return False
    return True


class CombatCortex:
    name = "combat"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        if not state.can_take_main_action or state.self.ep < 1 or state.alert_active or state.is_low_hp:
            return results

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

        target = sorted(targets, key=lambda item: target_score(state, item), reverse=True)[0]
        score = target_score(state, target)
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
