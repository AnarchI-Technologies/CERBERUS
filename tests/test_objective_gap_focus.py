from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from claw_runtime import objective_levels_from_status
from quest_rush_cortex import QuestRushCortex
from turn_state_model import TurnState


def state(levels: dict[str, int], **view_updates) -> TurnState:  # type: ignore[no-untyped-def]
    view = {
        "self": {"id": "hellion", "hp": 100, "maxHp": 100, "ep": 5},
        "currentRegion": {"id": "r1", "name": "Ruin", "terrain": "ruin", "items": []},
        "visibleAgents": [],
        "visibleMonsters": [],
        "aliveCount": 8,
    }
    view.update(view_updates)
    return TurnState.from_snapshot({"canAct": True, "view": view, "_cerberusObjectiveLevels": levels})


def test_status_progress_becomes_sanitized_level_map() -> None:
    status = {"preseason1_claims": {"progress": [
        {"key": "items", "level": 6, "progress": 999},
        {"key": "explore", "level": "0"},
        {"key": "bad", "level": "unknown"},
    ]}}
    assert objective_levels_from_status(status) == {"items": 6, "explore": 0}


def test_completed_tracks_stop_competing_for_quest_priority() -> None:
    levels = {"items": 6, "explore": 5, "damage": 5, "kills": 5, "top5": 5, "survival": 5}
    current = state(
        levels,
        currentRegion={"id": "r1", "name": "Ruin", "terrain": "ruin", "items": [{"id": "scrap", "typeId": "item"}]},
    )
    intents = {result.intent for result in QuestRushCortex().evaluate(current, {})}
    assert "quest_item_pickup" not in intents
    assert "quest_discover_ruin" not in intents
    assert "quest_top10_reserve" not in intents
    assert "quest_exploration_rotation" not in intents


def test_known_relic_exploration_continues_after_explore_track_is_complete() -> None:
    current = state({"explore": 5, "items": 6})
    current.ruins["ruin-1"] = type("Ruin", (), {
        "is_empty": False, "occupied_by": "", "max_gauge": 3, "gauge": 0, "content_type": "relic"
    })()
    intents = {result.intent for result in QuestRushCortex().evaluate(current, {})}
    assert "quest_relic_acquisition" in intents
