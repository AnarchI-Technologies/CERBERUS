"""
Threat Cortex: survival vetoes and immediate risk control.
"""

from __future__ import annotations

from cortex_types import CortexResult, action, rest_action
from death_zone_engine import death_zone_pressure, escape_region
from turn_state_model import TurnState


class ThreatCortex:
    name = "threat"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        if not state.can_take_main_action:
            return results

        pressure = death_zone_pressure(state)
        if pressure >= 80:
            region = escape_region(state)
            emergency_action = escape_action(state, region)
            if emergency_action["type"] == "move":
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="escape_death_zone",
                        score=100,
                        risk=5,
                        priority=100,
                        veto=True,
                        action=emergency_action,
                        reason=f"death-zone pressure {pressure}; move to {emergency_action.get('regionId')}",
                        source_facts=["F|safety.deathzone"],
                    )
                )
            else:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="emergency_death_zone_probe",
                        score=88,
                        risk=25,
                        priority=95,
                        veto=True,
                        action=emergency_action,
                        reason=str(emergency_action.get("reason") or "death-zone emergency fallback"),
                        source_facts=["F|safety.deathzone", "F|action.cost"],
                    )
                )

        heal_item = state.best_heal_item()
        if state.is_low_hp and heal_item:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="heal_low_hp",
                    score=85,
                    risk=10,
                    priority=92,
                    veto=True,
                    action=action("use_item", itemId=heal_item.get("id")),
                    reason=f"low HP {state.self.hp}; use {heal_item.get('typeId') or heal_item.get('name')}",
                    source_facts=["F|items.recovery", "F|action.cost"],
                )
            )

        if state.alert_active or state.alert_gauge >= 10:
            region = escape_region(state)
            if region:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="break_guardian_alert_exposure",
                        score=78,
                        risk=15,
                        priority=90,
                        action=action("move", regionId=region.get("id")),
                        reason=f"alert active at {state.alert_gauge}; move to reduce guardian exposure",
                        source_facts=["F|safety.alert", "F|guardian.current"],
                    )
                )
            else:
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="cool_alert",
                        score=60,
                        risk=15,
                        priority=82,
                        action=rest_action(f"alert active at {state.alert_gauge}; cool down if movement is unclear"),
                        reason="alert active and no safer move parsed",
                        source_facts=["F|safety.alert"],
                    )
                )

        if state.self.ep <= 1 and not results:
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="recover_ep_floor",
                    score=40,
                    risk=5,
                    priority=60,
                    action=rest_action("EP floor reached"),
                    reason="EP too low for flexible action",
                    source_facts=["F|action.cost"],
                )
            )

        return results


def scan(perception) -> list[CortexResult]:
    state = perception if isinstance(perception, TurnState) else TurnState.from_snapshot(perception)
    return ThreatCortex().evaluate(state, {})


def escape_action(state: TurnState, region: dict | None = None) -> dict:
    pending = state.pending_deathzone_ids
    current = state.current_region.id
    if region and region.get("id"):
        region_id = str(region.get("id") or "")
        if region_id and region_id != current and region_id not in pending and not region.get("isDeathZone"):
            return action("move", regionId=region_id)

    for candidate in state.visible_regions:
        region_id = str(candidate.get("id") or candidate.get("regionId") or "")
        if not region_id or region_id == current or region_id in pending:
            continue
        if candidate.get("isDeathZone"):
            continue
        return action("move", regionId=region_id, reason="death-zone pressure; probe visible non-death-zone region")

    for candidate in state.connected_safe_regions():
        region_id = str(candidate.get("id") or candidate.get("regionId") or "") if isinstance(candidate, dict) else str(candidate)
        if region_id and region_id != current:
            return action("move", regionId=region_id, reason="death-zone pressure; emergency move via raw connection id")

    if state.self.ep > 0:
        return action("explore", reason="death-zone pressure; no escape route parsed, spend action to reveal exits")

    return rest_action("death-zone pressure but no EP or region data available")
