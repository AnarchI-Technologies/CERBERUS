from __future__ import annotations

import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from game_adapters.base import GameAdapter
from game_adapters.claw_royale import ClawRoyaleAdapter


def test_claw_adapter_satisfies_provider_neutral_contract() -> None:
    adapter = ClawRoyaleAdapter()
    assert isinstance(adapter, GameAdapter)
    assert adapter.capabilities().realtime
    assert adapter.capabilities().paid_entry


def test_claw_adapter_normalizes_snapshot_without_network_calls() -> None:
    adapter = ClawRoyaleAdapter()
    observation = adapter.normalize_observation(
        {
            "type": "agent_view",
            "gameId": "g1",
            "turn": 7,
            "canAct": True,
            "view": {
                "self": {"id": "hellion", "hp": 90, "ep": 4},
                "currentRegion": {"id": "r1", "connections": ["r2"]},
            },
        }
    )
    assert observation.game_id == "g1"
    assert observation.turn == 7
    assert observation.state.self.id == "hellion"


def test_claw_adapter_fails_closed_on_invalid_action() -> None:
    adapter = ClawRoyaleAdapter()
    observation = adapter.normalize_observation({"view": {"self": {"id": "hellion"}}})
    assert adapter.validate_action(observation, {"type": "attack"}) == ("missing:targetId",)
    assert adapter.validate_action(observation, {"type": "invented"}) == ("unsupported_action:invented",)


def test_claw_adapter_normalizes_retryable_provider_outcome() -> None:
    result = ClawRoyaleAdapter().normalize_outcome({"success": False, "code": "TIMEOUT", "error": "late"})
    assert result == {"ok": False, "code": "TIMEOUT", "retryable": True, "detail": "late"}


def test_sanitized_claw_replay_fixture_is_stable() -> None:
    payload = json.loads((ROOT / "tests" / "fixtures" / "claw_agent_view_sanitized.json").read_text(encoding="utf-8"))
    observation = ClawRoyaleAdapter().normalize_observation(payload)
    assert observation.game_id == "fixture-game"
    assert observation.state.current_region.terrain == "ruin"
    assert "api" not in json.dumps(payload).lower()
    assert "wallet" not in json.dumps(payload).lower()
