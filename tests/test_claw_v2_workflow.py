import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from claw_v2_workflow import build_free_action_contracts
from turn_state_model import TurnState


def test_free_action_contracts_are_stable_and_linked():
    state = TurnState.from_snapshot(
        {
            "gameId": "game-1",
            "turn": 7,
            "view": {
                "self": {"id": "hellion", "hp": 100, "ep": 5},
                "currentRegion": {"id": "r1", "items": [{"id": "relic-1", "typeId": "relic"}]},
            },
        }
    )
    action = {"type": "pickup", "itemId": "relic-1", "reason": "ignored"}

    first = build_free_action_contracts(state, action)
    second = build_free_action_contracts(state, action)
    event, decision, request = first

    assert event.event_id == second[0].event_id
    assert decision.decision_id == second[1].decision_id
    assert request.request_id == second[2].request_id
    assert decision.event_id == event.event_id
    assert request.decision_id == decision.decision_id
    assert decision.assisted_by_model is False
    assert request.origin == "deterministic"
    assert request.consequential is False
    assert event.payload == {"turn": 7, "action_type": "pickup"}
    assert "reason" not in event.payload
