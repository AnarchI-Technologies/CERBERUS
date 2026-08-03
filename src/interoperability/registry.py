"""Deterministic adapter registration and capability routing."""

from __future__ import annotations

import inspect
from collections.abc import Iterable

from .contracts import Adapter, AdapterDescriptor, Command, ContractError, Result


class RegistryError(ContractError):
    """Raised when registration or routing violates the contract."""


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, adapter: Adapter) -> AdapterDescriptor:
        if not isinstance(adapter, Adapter):
            raise RegistryError("adapter does not satisfy the interoperability protocol")
        descriptor = adapter.descriptor
        if descriptor.adapter_id in self._adapters:
            raise RegistryError(f"adapter already registered: {descriptor.adapter_id}")
        self._adapters[descriptor.adapter_id] = adapter
        return descriptor

    def unregister(self, adapter_id: str) -> AdapterDescriptor:
        try:
            adapter = self._adapters.pop(adapter_id)
        except KeyError as error:
            raise RegistryError(f"adapter is not registered: {adapter_id}") from error
        return adapter.descriptor

    def descriptors(self) -> tuple[AdapterDescriptor, ...]:
        return tuple(self._adapters[key].descriptor for key in sorted(self._adapters))

    def adapters_for(self, capability: str, version: str | None = None) -> tuple[AdapterDescriptor, ...]:
        return tuple(
            descriptor
            for descriptor in self.descriptors()
            if descriptor.supports(capability, version)
        )

    def require(self, adapter_id: str, capability: str) -> Adapter:
        try:
            adapter = self._adapters[adapter_id]
        except KeyError as error:
            raise RegistryError(f"adapter is not registered: {adapter_id}") from error
        if not adapter.descriptor.supports(capability):
            raise RegistryError(
                f"adapter {adapter_id!r} does not declare capability {capability!r}"
            )
        return adapter

    async def dispatch(self, command: Command) -> Result:
        adapter = self.require(command.adapter_id, command.capability)
        outcome = adapter.invoke(command)
        if not inspect.isawaitable(outcome):
            raise RegistryError(f"adapter {command.adapter_id!r} returned a non-awaitable result")
        result = await outcome
        if not isinstance(result, Result):
            raise RegistryError(f"adapter {command.adapter_id!r} returned an invalid result")
        mismatches = tuple(
            name
            for name, actual, expected in (
                ("command_id", result.command_id, command.command_id),
                ("adapter_id", result.adapter_id, command.adapter_id),
                ("capability", result.capability, command.capability),
            )
            if actual != expected
        )
        if mismatches:
            raise RegistryError(
                "adapter result does not match command fields: " + ", ".join(mismatches)
            )
        return result

    def extend(self, adapters: Iterable[Adapter]) -> tuple[AdapterDescriptor, ...]:
        return tuple(self.register(adapter) for adapter in adapters)
