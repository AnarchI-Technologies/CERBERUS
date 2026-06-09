"""
Action risk helpers for combat, ruin pressure, and survival bias.
"""

from __future__ import annotations

from turn_state_model import TurnState


def progression_value_at_risk(state: TurnState) -> float:
    cargo = 0
    for item in state.inventory:
        label = str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
        if "pack" in label:
            cargo += 3
        elif "relic" in label:
            cargo += 2

    nearby_hostiles = len([agent for agent in state.visible_agents if agent.is_alive and agent.id != state.self.id])
    return round(
        cargo * 14
        + max(0, 45 - state.self.hp) * 0.8
        + max(0, state.alert_gauge - 7) * 4
        + nearby_hostiles * 12
        + (20 if state.is_in_death_zone else 0),
        2,
    )
