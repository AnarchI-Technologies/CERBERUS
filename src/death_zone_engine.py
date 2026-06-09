"""
Death-zone helpers.
"""

from __future__ import annotations

from turn_state_model import TurnState


def escape_region(state: TurnState) -> dict | None:
    safe = state.connected_safe_regions()
    if not safe:
        return None
    # Prefer visible regions with more onward connections.
    return sorted(
        safe,
        key=lambda r: len(r.get("connections", [])) if isinstance(r, dict) else 0,
        reverse=True,
    )[0]


def death_zone_pressure(state: TurnState) -> float:
    if state.is_in_death_zone:
        return 100.0
    if state.current_region.id in state.pending_deathzone_ids:
        return 80.0
    return float(len(state.pending_deathzones) * 5)
