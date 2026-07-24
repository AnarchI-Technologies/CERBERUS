"""Named, independently callable survival strategies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from cortex_types import CortexResult, action, rest_action
from death_zone_engine import death_zone_pressure, escape_region
from turn_state_model import TurnState


StrategyCallable = Callable[[TurnState, dict[str, Any]], tuple[CortexResult, ...]]
CORTEX = "threat"


def escape_action(state: TurnState, region: dict | None = None) -> dict:
    pending = state.pending_deathzone_ids
    current = state.current_region.id
    if region and region.get("id"):
        region_id = str(region.get("id") or "")
        if (
            region_id
            and region_id != current
            and region_id not in pending
            and not region.get("isDeathZone")
        ):
            return action("move", regionId=region_id)

    for candidate in state.visible_regions:
        region_id = str(candidate.get("id") or candidate.get("regionId") or "")
        if not region_id or region_id == current or region_id in pending:
            continue
        if candidate.get("isDeathZone"):
            continue
        return action(
            "move",
            regionId=region_id,
            reason="death-zone pressure; probe visible non-death-zone region",
        )

    for candidate in state.connected_safe_regions():
        region_id = (
            str(candidate.get("id") or candidate.get("regionId") or "")
            if isinstance(candidate, dict)
            else str(candidate)
        )
        if region_id and region_id != current:
            return action(
                "move",
                regionId=region_id,
                reason="death-zone pressure; emergency move via raw connection id",
            )

    if state.self.ep > 0:
        return action(
            "explore",
            reason="death-zone pressure; no escape route parsed, spend action to reveal exits",
        )
    return rest_action("death-zone pressure but no EP or region data available")


def escape_death_zone(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    if not state.can_take_main_action or death_zone_pressure(state) < 80:
        return ()
    pressure = death_zone_pressure(state)
    emergency = escape_action(state, escape_region(state))
    if emergency["type"] != "move":
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="escape_death_zone",
            score=100,
            risk=5,
            priority=100,
            veto=True,
            action=emergency,
            reason=f"death-zone pressure {pressure}; move to {emergency.get('regionId')}",
            source_facts=["F|safety.deathzone"],
        ),
    )


def emergency_death_zone_probe(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    if not state.can_take_main_action or death_zone_pressure(state) < 80:
        return ()
    emergency = escape_action(state, escape_region(state))
    if emergency["type"] == "move":
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="emergency_death_zone_probe",
            score=88,
            risk=25,
            priority=95,
            veto=True,
            action=emergency,
            reason=str(
                emergency.get("reason") or "death-zone emergency fallback"
            ),
            source_facts=["F|safety.deathzone", "F|action.cost"],
        ),
    )


def heal_low_hp(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    heal_item = state.best_heal_item()
    if not state.can_take_main_action or not state.is_low_hp or not heal_item:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="heal_low_hp",
            score=85,
            risk=10,
            priority=92,
            veto=True,
            action=action("use_item", itemId=heal_item.get("id")),
            reason=(
                f"low HP {state.self.hp}; use "
                f"{heal_item.get('typeId') or heal_item.get('name')}"
            ),
            source_facts=["F|items.recovery", "F|action.cost"],
        ),
    )


def break_guardian_alert_exposure(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    if not state.can_take_main_action or not (
        state.alert_active or state.alert_gauge >= 10
    ):
        return ()
    region = escape_region(state)
    if not region:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="break_guardian_alert_exposure",
            score=78,
            risk=15,
            priority=90,
            action=action("move", regionId=region.get("id")),
            reason=(
                f"alert active at {state.alert_gauge}; "
                "move to reduce guardian exposure"
            ),
            source_facts=["F|safety.alert", "F|guardian.current"],
        ),
    )


def cool_alert(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    if not state.can_take_main_action or not (
        state.alert_active or state.alert_gauge >= 10
    ):
        return ()
    if escape_region(state):
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="cool_alert",
            score=60,
            risk=15,
            priority=82,
            action=rest_action(
                f"alert active at {state.alert_gauge}; cool down if movement is unclear"
            ),
            reason="alert active and no safer move parsed",
            source_facts=["F|safety.alert"],
        ),
    )


def recover_ep_floor(
    state: TurnState, context: dict[str, Any]
) -> tuple[CortexResult, ...]:
    if not state.can_take_main_action or state.self.ep > 1:
        return ()
    blocking = (
        death_zone_pressure(state) >= 80
        or (state.is_low_hp and bool(state.best_heal_item()))
        or state.alert_active
        or state.alert_gauge >= 10
    )
    if blocking:
        return ()
    return (
        CortexResult(
            cortex=CORTEX,
            intent="recover_ep_floor",
            score=40,
            risk=5,
            priority=60,
            action=rest_action("EP floor reached"),
            reason="EP too low for flexible action",
            source_facts=["F|action.cost"],
        ),
    )


THREAT_STRATEGIES: dict[str, StrategyCallable] = {
    "escape_death_zone": escape_death_zone,
    "emergency_death_zone_probe": emergency_death_zone_probe,
    "heal_low_hp": heal_low_hp,
    "break_guardian_alert_exposure": break_guardian_alert_exposure,
    "cool_alert": cool_alert,
    "recover_ep_floor": recover_ep_floor,
}


def evaluate_threat_strategies(
    state: TurnState, context: dict[str, Any]
) -> list[CortexResult]:
    results: list[CortexResult] = []
    for strategy in THREAT_STRATEGIES.values():
        results.extend(strategy(state, context))
    return results
