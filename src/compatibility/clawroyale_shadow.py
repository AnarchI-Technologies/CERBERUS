"""Read-only parity bridge between legacy and portable Claw boundaries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from clawroyale_ai import ACTION_TYPES, ClawRoyaleAdapter, validate_action
from game_adapters.claw_royale import ClawRoyaleAdapter as LegacyClawRoyaleAdapter


async def _unreachable_transport(_: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("shadow comparison must never invoke transport")


@dataclass(frozen=True, slots=True)
class ShadowCheck:
    name: str
    legacy: Any
    portable: Any

    @property
    def passed(self) -> bool:
        return self.legacy == self.portable


@dataclass(frozen=True, slots=True)
class ShadowReport:
    checks: tuple[ShadowCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def mismatches(self) -> tuple[str, ...]:
        return tuple(check.name for check in self.checks if not check.passed)


class ClawRoyaleShadowBridge:
    """Compare both seams without changing or executing either live path."""

    def __init__(self) -> None:
        self._legacy = LegacyClawRoyaleAdapter()
        self._portable = ClawRoyaleAdapter(_unreachable_transport)

    def compare_observation(self, payload: dict[str, Any]) -> ShadowReport:
        legacy = self._legacy.normalize_observation(payload)
        portable = self._portable.normalize_observation(
            payload,
            session_id="shadow-session",
            sequence=max(0, legacy.turn),
            observation_id="shadow-observation",
        )
        normalized = portable.payload
        return ShadowReport(
            checks=(
                ShadowCheck("match_id", legacy.game_id, normalized["match_id"]),
                ShadowCheck("turn", legacy.turn, normalized["turn"]),
                ShadowCheck("can_act", legacy.can_act, normalized["can_act"]),
                ShadowCheck("actor_id", legacy.state.self.id, normalized["actor"].get("id", "")),
                ShadowCheck(
                    "location_id",
                    legacy.state.current_region.id,
                    normalized["location"].get("id", ""),
                ),
                ShadowCheck("alive_count", legacy.state.alive_count, normalized["alive_count"]),
            )
        )

    def compare_action(self, payload: dict[str, Any], action: dict[str, Any]) -> ShadowReport:
        legacy_observation = self._legacy.normalize_observation(payload)
        legacy_errors = self._legacy.validate_action(legacy_observation, action)
        advertised = self._legacy.available_action_types(legacy_observation)
        portable_errors = validate_action(
            action,
            available=advertised if advertised else ACTION_TYPES,
        )
        return ShadowReport(
            checks=(
                ShadowCheck("validation_errors", legacy_errors, portable_errors),
            )
        )
