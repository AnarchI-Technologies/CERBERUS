"""Deterministic Claw Royale strategy registration and selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from interoperability import Command, Observation

from .contracts import ACTION_CAPABILITY, ADAPTER_ID


class StrategyError(ValueError):
    pass


@runtime_checkable
class Strategy(Protocol):
    strategy_id: str
    priority: int

    def propose(self, observation: Observation) -> Command | None: ...


@dataclass(frozen=True, slots=True)
class StrategySelection:
    strategy_id: str
    command: Command


class StrategyRegistry:
    def __init__(self) -> None:
        self._strategies: dict[str, Strategy] = {}

    def register(self, strategy: Strategy) -> None:
        if not isinstance(strategy, Strategy):
            raise StrategyError("strategy does not satisfy the strategy protocol")
        strategy_id = str(strategy.strategy_id).strip()
        if not strategy_id:
            raise StrategyError("strategy id must not be empty")
        if strategy_id in self._strategies:
            raise StrategyError(f"strategy already registered: {strategy_id}")
        self._strategies[strategy_id] = strategy

    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(
            strategy.strategy_id
            for strategy in sorted(
                self._strategies.values(),
                key=lambda item: (-int(item.priority), str(item.strategy_id)),
            )
        )

    def select(self, observation: Observation) -> StrategySelection | None:
        if observation.adapter_id != ADAPTER_ID:
            raise StrategyError(f"wrong observation adapter: {observation.adapter_id}")
        for strategy_id in self.strategy_ids():
            command = self._strategies[strategy_id].propose(observation)
            if command is None:
                continue
            if command.adapter_id != observation.adapter_id:
                raise StrategyError(f"strategy {strategy_id!r} changed adapter identity")
            if command.session_id != observation.session_id:
                raise StrategyError(f"strategy {strategy_id!r} changed session identity")
            if command.capability != ACTION_CAPABILITY:
                raise StrategyError(f"strategy {strategy_id!r} returned a non-action capability")
            return StrategySelection(strategy_id=strategy_id, command=command)
        return None
