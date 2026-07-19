"""Pure translation adapter for Claw Royale observations and actions."""

from __future__ import annotations

from typing import Any

from claw_contract import KNOWN_ACTION_TYPES, REQUIRED_ACTION_FIELDS
from game_adapters.base import AdapterCapabilities, NormalizedObservation
from turn_state_model import TurnState


class ClawRoyaleAdapter:
    provider = "claw_royale"
    schema_version = "claw_royale.adapter.v1"

    def capabilities(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            realtime=True,
            tournaments=True,
            marketplace=True,
            pvp=True,
            economy=True,
            paid_entry=True,
        )

    def normalize_observation(self, payload: dict[str, Any]) -> NormalizedObservation:
        if not isinstance(payload, dict):
            raise TypeError("Claw observation must be an object")
        state = TurnState.from_snapshot(payload)
        return NormalizedObservation(
            provider=self.provider,
            schema_version=self.schema_version,
            game_id=state.game_id,
            turn=state.turn,
            can_act=state.can_take_main_action,
            state=state,
            source_event_type=str(payload.get("type") or "snapshot")[:60],
        )

    def available_action_types(self, observation: NormalizedObservation) -> frozenset[str]:
        state = observation.state
        advertised = state.available_actions if isinstance(state, TurnState) else {}
        if advertised:
            return frozenset(str(key) for key in advertised if str(key) in KNOWN_ACTION_TYPES)
        return frozenset(KNOWN_ACTION_TYPES)

    def validate_action(self, observation: NormalizedObservation, action: dict[str, Any]) -> tuple[str, ...]:
        if not isinstance(action, dict):
            return ("action_not_object",)
        action_type = str(action.get("type") or "")
        if action_type not in self.available_action_types(observation):
            return (f"unsupported_action:{action_type or 'missing'}",)
        missing = tuple(field for field in REQUIRED_ACTION_FIELDS.get(action_type, ()) if not action.get(field))
        return tuple(f"missing:{field}" for field in missing)

    def normalize_outcome(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict):
            return {"ok": False, "code": "MALFORMED_OUTCOME", "retryable": False, "detail": "non-object"}
        error = payload.get("error") or payload.get("message") or ""
        ok = payload.get("success", payload.get("ok")) is not False and not error
        code = payload.get("code") or payload.get("errorCode") or ("accepted" if ok else "rejected")
        return {
            "ok": bool(ok),
            "code": str(code)[:80],
            "retryable": str(code).upper() in {"TIMEOUT", "SERVER_ERROR", "TEMPORARILY_UNAVAILABLE"},
            "detail": str(error)[:180],
        }
