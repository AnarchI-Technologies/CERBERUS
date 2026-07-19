from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

import claw_contract
import claw_runtime
from onboarding_clients import ClawRoyaleClient


def test_v1131_connection_ownership_and_backoff() -> None:
    config = claw_runtime.ClawRuntimeConfig(api_key="fixture", min_reconnect_seconds=5, max_reconnect_seconds=30)
    error = RuntimeError("received 4030: web session active")

    assert claw_contract.JOIN_CLOSE_CODES["WEB_SESSION_ACTIVE"] == 4030
    assert claw_contract.JOIN_CLOSE_CODES["BOT_SESSION_ACTIVE"] == 4031
    assert claw_runtime.web_session_controls_agent(error) is True
    assert claw_runtime.reconnect_delay_seconds(config, 1, error) >= 60


def test_paid_winners_are_limited_and_sanitized() -> None:
    payload = {
        "type": "game_ended",
        "winners": [
            {"rank": index, "agentId": f"a-{index}", "name": f"Agent {index}", "isAI": True,
             "prizeMoltz": 10 - index, "reforgeStones": 1, "walletAddress": "secret-adjacent"}
            for index in range(1, 8)
        ],
    }
    winners = claw_runtime.paid_room_winners(payload)

    assert len(winners) == 5
    assert winners[0]["prizeMoltz"] == 9
    assert "walletAddress" not in winners[0]


def test_finished_state_recovers_paid_winners_when_event_was_missed(monkeypatch) -> None:
    updates = []

    class Client:
        def __init__(self, **_kwargs):  # type: ignore[no-untyped-def]
            pass

        def game_state(self, game_id: str):  # type: ignore[no-untyped-def]
            assert game_id == "paid-1"
            return {"room": {"winners": [{"rank": 1, "agentId": "winner", "prizeMoltz": 50}]}}

    monkeypatch.setattr(claw_runtime, "ClawRoyaleClient", Client)
    monkeypatch.setattr(claw_runtime, "update_status", lambda **kwargs: updates.append(kwargs))
    config = claw_runtime.ClawRuntimeConfig(api_key="fixture")

    winners = claw_runtime.record_paid_room_winners(config, "paid-1", {})

    assert winners == [{"rank": 1, "agentId": "winner", "prizeMoltz": 50}]
    assert updates[-1]["paid_room_winner_source"] == "finished_game_state"
    assert updates[-1]["paid_room_winner_unit"] == "Moltz"


def test_v1131_client_routes_are_exact(monkeypatch) -> None:
    calls = []
    client = ClawRoyaleClient(api_key="fixture")
    monkeypatch.setattr(client, "_request", lambda method, path, **kwargs: calls.append((method, path, kwargs)) or {})

    client.game_state("game/one")
    client.redeem("WELCOME", "cerberus-welcome-v1")

    assert calls[0][:2] == ("GET", "/games/game%2Fone/state")
    assert calls[1][:2] == ("POST", "/redeem")
    assert calls[1][2]["json"] == {"code": "WELCOME"}
    assert calls[1][2]["headers"] == {"Idempotency-Key": "cerberus-welcome-v1"}


def test_v1131_mechanics_and_units_match_official_contract() -> None:
    assert claw_contract.ITEM_MECHANICS_1_13_1["binoculars"]["reveals_stealthed_assassins_in_vision"] is True
    assert claw_contract.ITEM_MECHANICS_1_13_1["binoculars"]["bypasses_cave_concealment"] is False
    assert claw_contract.ITEM_MECHANICS_1_13_1["vision_ward"]["lootable"] is False
    assert claw_contract.CLASS_MECHANICS_1_13_1["assassin"]["exposure_refreshes_on_every_damaging_attack"] is True
    assert claw_contract.CLASS_MECHANICS_1_13_1["sword_master"]["ranged_immunity_requires_equipped_melee_weapon"] is True
    assert claw_contract.PAID_ECONOMY_UNITS["entry_fee"] == "sMoltz"
    assert claw_contract.PAID_ECONOMY_UNITS["game_ended_prize"] == "Moltz"
    assert claw_contract.PAID_ECONOMY_UNITS["cross_unit_subtraction_allowed"] is False
    assert claw_contract.WELCOME_BUNDLE == {
        "code": "WELCOME", "once_per_account": True, "packs": 2, "relics": 3, "effect_reroll_stones": 20,
    }
