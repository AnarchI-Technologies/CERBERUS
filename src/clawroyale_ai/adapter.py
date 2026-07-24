"""Claw Royale translation adapter with an injected transport boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from interoperability import (
    AdapterDescriptor,
    Command,
    ContractError,
    Observation,
    Result,
    ResultStatus,
)

from .contracts import (
    ACTION_CAPABILITY,
    ADAPTER_ID,
    ADAPTER_VERSION,
    CAPABILITIES,
    JOIN_CAPABILITY,
    LEAVE_CAPABILITY,
    validate_action,
)


Transport = Callable[[dict[str, Any]], Awaitable[Mapping[str, Any]]]
RETRYABLE_CODES = frozenset({"SERVER_ERROR", "TEMPORARILY_UNAVAILABLE", "TIMEOUT"})


def _object(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list_of_objects(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _allowed(source: Mapping[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    return {field: source[field] for field in fields if field in source}


class ClawRoyaleAdapter:
    descriptor = AdapterDescriptor(
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        capabilities=CAPABILITIES,
        metadata={"owner": "clawroyale.ai", "transport": "injected"},
    )

    def __init__(self, transport: Transport) -> None:
        if not callable(transport):
            raise TypeError("transport must be callable")
        self._transport = transport

    def normalize_observation(
        self,
        payload: Mapping[str, Any],
        *,
        session_id: str,
        sequence: int,
        observation_id: str,
        correlation_id: str = "",
    ) -> Observation:
        if not isinstance(payload, Mapping):
            raise ContractError("Claw Royale observation must be an object")

        outer = _object(payload.get("data")) or payload
        state = _object(outer.get("state")) or outer
        view = _object(state.get("view")) or state
        actor = _object(view.get("self") or view.get("agent"))
        location = _object(view.get("currentRegion") or view.get("region"))
        available_raw = view.get("availableActions", state.get("availableActions", ()))

        if isinstance(available_raw, Mapping):
            available_actions = sorted(str(key) for key in available_raw)
        elif isinstance(available_raw, (list, tuple)):
            available_actions = sorted(
                str(item.get("type") if isinstance(item, Mapping) else item)
                for item in available_raw
                if str(item).strip()
            )
        else:
            available_actions = []

        cooldown_remaining_ms = int(
            state.get("cooldownRemainingMs")
            or view.get("cooldownRemainingMs")
            or actor.get("cooldownRemainingMs")
            or 0
        )
        advertised_can_act = bool(
            state.get("canAct")
            if "canAct" in state
            else view.get("canAct", False)
        )
        normalized = {
            "source_type": str(payload.get("type") or state.get("type") or "snapshot")[:80],
            "match_id": str(
                state.get("gameId")
                or state.get("game_id")
                or view.get("gameId")
                or ""
            ),
            "turn": int(state.get("turn") or view.get("turn") or 0),
            "phase": str(state.get("status") or view.get("status") or ""),
            "can_act": advertised_can_act and cooldown_remaining_ms <= 0,
            "can_act_advertised": advertised_can_act,
            "cooldown_remaining_ms": max(0, cooldown_remaining_ms),
            "actor": _allowed(
                actor,
                ("id", "name", "hp", "maxHp", "ep", "maxEp", "isAlive", "cooldownRemaining"),
            ),
            "location": _allowed(
                location,
                ("id", "terrain", "connections", "deathZone", "pendingDeathZone"),
            ),
            "inventory": _list_of_objects(actor.get("inventory")),
            "entities": {
                "agents": _list_of_objects(view.get("visibleAgents")),
                "monsters": _list_of_objects(view.get("visibleMonsters")),
                "items": _list_of_objects(view.get("visibleItems")),
            },
            "available_actions": available_actions,
            "alive_count": int(view.get("aliveCount") or state.get("aliveCount") or 0),
        }

        return Observation(
            observation_id=observation_id,
            adapter_id=ADAPTER_ID,
            capability=ACTION_CAPABILITY,
            session_id=session_id,
            sequence=sequence,
            payload=normalized,
            correlation_id=correlation_id,
        )

    async def invoke(self, command: Command) -> Result:
        if command.adapter_id != ADAPTER_ID:
            raise ContractError(f"wrong adapter id: {command.adapter_id}")
        if not self.descriptor.supports(command.capability):
            raise ContractError(f"unsupported capability: {command.capability}")

        payload = dict(command.payload)
        if command.capability == ACTION_CAPABILITY:
            action = payload.get("action")
            errors = validate_action(action)
            if errors:
                return Result(
                    result_id=f"result:{command.command_id}",
                    command_id=command.command_id,
                    adapter_id=ADAPTER_ID,
                    capability=command.capability,
                    status=ResultStatus.REJECTED,
                    error_code=errors[0],
                    error_message=";".join(errors),
                    correlation_id=command.correlation_id,
                )
            frame = {"type": "action", "action": dict(action)}
        elif command.capability == JOIN_CAPABILITY:
            entry_type = str(payload.get("entry_type") or "")
            if entry_type not in {"free", "paid"}:
                return Result(
                    result_id=f"result:{command.command_id}",
                    command_id=command.command_id,
                    adapter_id=ADAPTER_ID,
                    capability=command.capability,
                    status=ResultStatus.REJECTED,
                    error_code="invalid:entry_type",
                    correlation_id=command.correlation_id,
                )
            frame = {"type": "join", "entryType": entry_type}
        elif command.capability == LEAVE_CAPABILITY:
            frame = {"type": "leave", "sessionId": command.session_id}
        else:
            raise ContractError(f"unmapped capability: {command.capability}")

        response = dict(await self._transport(frame))
        error_message = str(response.get("error") or response.get("message") or "")
        ok = response.get("success", response.get("ok")) is not False and not error_message
        code = str(
            response.get("code")
            or response.get("errorCode")
            or ("accepted" if ok else "rejected")
        )[:80]
        retryable = code.upper() in RETRYABLE_CODES
        status = (
            ResultStatus.SUCCEEDED
            if ok
            else ResultStatus.FAILED
            if retryable
            else ResultStatus.REJECTED
        )
        return Result(
            result_id=f"result:{command.command_id}",
            command_id=command.command_id,
            adapter_id=ADAPTER_ID,
            capability=command.capability,
            status=status,
            payload={"provider_code": code},
            error_code="" if ok else code,
            error_message=error_message[:180],
            retryable=retryable,
            correlation_id=command.correlation_id,
        )
