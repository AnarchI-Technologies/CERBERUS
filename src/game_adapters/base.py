"""Stable boundary between game providers and the deterministic kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class AdapterCapabilities:
    realtime: bool = False
    tournaments: bool = False
    marketplace: bool = False
    pvp: bool = False
    economy: bool = False
    paid_entry: bool = False


@dataclass(frozen=True, slots=True)
class NormalizedObservation:
    provider: str
    schema_version: str
    game_id: str
    turn: int
    can_act: bool
    state: Any
    source_event_type: str


@runtime_checkable
class GameAdapter(Protocol):
    provider: str
    schema_version: str

    def capabilities(self) -> AdapterCapabilities: ...

    def normalize_observation(self, payload: dict[str, Any]) -> NormalizedObservation: ...

    def available_action_types(self, observation: NormalizedObservation) -> frozenset[str]: ...

    def validate_action(self, observation: NormalizedObservation, action: dict[str, Any]) -> tuple[str, ...]: ...

    def normalize_outcome(self, payload: dict[str, Any]) -> dict[str, Any]: ...
