from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from claw_policy_shadow import authorize_broadcast, authorize_broadcast_execution, evaluate_claw_action_shadow
from core_loop import cerberus_tick
from runtime_state import policy_shadow_records
from turn_state_model import TurnState


def snapshot() -> dict:
    return {
        "gameId": "game-shadow",
        "turn": 4,
        "canAct": True,
        "view": {
            "self": {"id": "hellion", "hp": 100, "maxHp": 100, "ep": 5},
            "currentRegion": {"id": "r1", "connections": ["r2"]},
            "aliveCount": 30,
        },
    }


def test_shadow_allows_visible_move_without_mutating_action() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("CERBERUS_MEMORY_DIR")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            action = {"type": "move", "regionId": "r2", "reason": "safe rotation"}
            original = dict(action)
            record = evaluate_claw_action_shadow(TurnState.from_snapshot(snapshot()), action)
            saved = policy_shadow_records()
        finally:
            if old is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old

    assert action == original
    assert record["policy"]["outcome"] == "ALLOW"
    assert saved[-1]["enforced"] is False


def test_core_tick_returns_same_selected_action_with_shadow_enabled() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        old_shadow = os.environ.get("CERBERUS_V2_POLICY_SHADOW_ENABLED")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            os.environ["CERBERUS_V2_POLICY_SHADOW_ENABLED"] = "false"
            without_shadow = cerberus_tick(snapshot())
            os.environ["CERBERUS_V2_POLICY_SHADOW_ENABLED"] = "true"
            with_shadow = cerberus_tick(snapshot())
            saved = policy_shadow_records()
        finally:
            if old_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_dir
            if old_shadow is None:
                os.environ.pop("CERBERUS_V2_POLICY_SHADOW_ENABLED", None)
            else:
                os.environ["CERBERUS_V2_POLICY_SHADOW_ENABLED"] = old_shadow

    assert with_shadow["type"] == without_shadow["type"]
    assert with_shadow.get("regionId") == without_shadow.get("regionId")
    assert saved


def test_shadow_allows_use_of_carried_inventory_item() -> None:
    payload = snapshot()
    payload["view"]["self"]["inventory"] = [{"id": "med-1", "typeId": "medkit"}]
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("CERBERUS_MEMORY_DIR")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            record = evaluate_claw_action_shadow(
                TurnState.from_snapshot(payload),
                {"type": "use_item", "itemId": "med-1"},
            )
        finally:
            if old is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old

    assert record["policy"]["outcome"] == "ALLOW"


def test_broadcast_is_enforced_and_emergency_suspension_denies_it() -> None:
    state = TurnState.from_snapshot(snapshot())
    action = {"type": "broadcast", "message": "Arena update"}
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        old_suspend = os.environ.get("CERBERUS_EMERGENCY_SUSPEND")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            os.environ["CERBERUS_EMERGENCY_SUSPEND"] = "false"
            allowed, allowed_record = authorize_broadcast(state, action)
            os.environ["CERBERUS_EMERGENCY_SUSPEND"] = "true"
            denied, denied_record = authorize_broadcast(state, action)
        finally:
            if old_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_dir
            if old_suspend is None:
                os.environ.pop("CERBERUS_EMERGENCY_SUSPEND", None)
            else:
                os.environ["CERBERUS_EMERGENCY_SUSPEND"] = old_suspend

    assert allowed
    assert allowed_record["enforced"] is True
    assert not denied
    assert denied_record["policy"]["reasons"] == ["emergency_suspension"]


def test_broadcast_execution_contract_has_stable_idempotency_key() -> None:
    state = TurnState.from_snapshot(snapshot())
    action = {"type": "broadcast", "message": "Arena update"}
    with tempfile.TemporaryDirectory() as tmp:
        old_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        os.environ["CERBERUS_MEMORY_DIR"] = tmp
        try:
            first = authorize_broadcast_execution(state, action)
            second = authorize_broadcast_execution(state, action)
        finally:
            if old_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_dir

    assert first[0] is True
    assert first[1].idempotency_key == second[1].idempotency_key
    assert first[2].request_id == first[1].request_id
