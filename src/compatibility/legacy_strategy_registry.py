"""Callable strategy facade over cached legacy cortex providers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from clawroyale_ai.catalog import STRATEGY_BY_ID, STRATEGY_CATALOG, StrategyDescriptor


class StrategyRegistryError(ValueError):
    pass


class StrategyProvider(Protocol):
    def evaluate(self, state: Any, context: dict[str, Any]) -> list[Any]: ...


@dataclass(slots=True)
class StrategyCallCache:
    provider_results: dict[str, tuple[Any, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RegistryEvaluation:
    results: tuple[Any, ...]
    providers_evaluated: tuple[str, ...]
    strategies_eligible: int
    strategies_skipped: int


def state_signals(state: Any, context: dict[str, Any]) -> frozenset[str]:
    signals: set[str] = set()
    inventory = tuple(getattr(state, "inventory", ()) or ())
    visible_items = tuple(getattr(state, "visible_items", ()) or ())
    region = getattr(state, "current_region", None)
    region_items = tuple(getattr(region, "items", ()) or ())
    agents = tuple(getattr(state, "visible_agents", ()) or ())
    monsters = tuple(getattr(state, "visible_monsters", ()) or ())
    events = tuple(getattr(state, "events", ()) or ())
    messages = tuple(getattr(state, "recent_messages", ()) or ())

    if context.get("owner_messages"):
        signals.add("owner_messages")
    if inventory:
        signals.add("inventory")
    if inventory or visible_items or region_items:
        signals.add("free_action_inputs")
    if agents or monsters:
        signals.add("visible_targets")
    if visible_items or region_items or agents:
        signals.add("economy_inputs")
    if events or messages or agents:
        signals.add("social_inputs")
    return frozenset(signals)


class LegacyStrategyRegistry:
    """Expose each legacy intent by ID while evaluating each provider at most once."""

    def __init__(self, providers: dict[str, StrategyProvider]) -> None:
        self._providers = dict(providers)

    def descriptor(self, strategy_id: str) -> StrategyDescriptor:
        try:
            return STRATEGY_BY_ID[strategy_id]
        except KeyError as error:
            raise StrategyRegistryError(f"unknown strategy: {strategy_id}") from error

    def strategy_ids(self) -> tuple[str, ...]:
        return tuple(descriptor.strategy_id for descriptor in STRATEGY_CATALOG)

    def _provider_results(
        self,
        provider_name: str,
        state: Any,
        context: dict[str, Any],
        cache: StrategyCallCache,
    ) -> tuple[Any, ...]:
        if provider_name not in cache.provider_results:
            try:
                provider = self._providers[provider_name]
            except KeyError as error:
                raise StrategyRegistryError(f"provider is not registered: {provider_name}") from error
            cache.provider_results[provider_name] = tuple(provider.evaluate(state, context))
        return cache.provider_results[provider_name]

    def call(
        self,
        strategy_id: str,
        state: Any,
        context: dict[str, Any],
        *,
        cache: StrategyCallCache | None = None,
    ) -> tuple[Any, ...]:
        descriptor = self.descriptor(strategy_id)
        if not descriptor.required_signals.issubset(state_signals(state, context)):
            return ()
        active_cache = cache or StrategyCallCache()
        return tuple(
            result
            for result in self._provider_results(
                descriptor.provider, state, context, active_cache
            )
            if str(getattr(result, "intent", "")) == descriptor.intent
        )

    def evaluate_all(self, state: Any, context: dict[str, Any]) -> RegistryEvaluation:
        signals = state_signals(state, context)
        eligible = tuple(
            descriptor
            for descriptor in STRATEGY_CATALOG
            if descriptor.provider in self._providers
            and descriptor.required_signals.issubset(signals)
        )
        intents_by_provider: dict[str, set[str]] = {}
        provider_order: list[str] = []
        for descriptor in eligible:
            if descriptor.provider not in intents_by_provider:
                intents_by_provider[descriptor.provider] = set()
                provider_order.append(descriptor.provider)
            intents_by_provider[descriptor.provider].add(descriptor.intent)

        cache = StrategyCallCache()
        results: list[Any] = []
        for provider_name in provider_order:
            results.extend(
                result
                for result in self._provider_results(
                    provider_name, state, context, cache
                )
                if str(getattr(result, "intent", ""))
                in intents_by_provider[provider_name]
            )
        return RegistryEvaluation(
            results=tuple(results),
            providers_evaluated=tuple(provider_order),
            strategies_eligible=len(eligible),
            strategies_skipped=len(STRATEGY_CATALOG) - len(eligible),
        )
