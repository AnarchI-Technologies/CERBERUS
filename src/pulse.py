"""Deterministic runtime lifecycle coordination for AnarchI services.

Pulse owns service start and stop ordering. It does not route messages, make
gameplay decisions, or conceal lifecycle failures.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable


class PulseState(StrEnum):
    NEW = "new"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


@runtime_checkable
class PulseService(Protocol):
    """A named service whose lifecycle Pulse can coordinate."""

    name: str

    async def start(self) -> None:
        """Start the service or raise an exception."""

    async def stop(self) -> None:
        """Stop the service or raise an exception."""


@dataclass(frozen=True, slots=True)
class PulseEvent:
    sequence: int
    service: str
    phase: str
    status: str


@dataclass(frozen=True, slots=True)
class PulseSnapshot:
    state: PulseState
    registered: tuple[str, ...]
    active: tuple[str, ...]
    events: tuple[PulseEvent, ...]


class PulseLifecycleError(RuntimeError):
    """Base error for deterministic Pulse lifecycle failures."""


class PulseStartError(PulseLifecycleError):
    def __init__(self, service: str) -> None:
        super().__init__(f"Pulse could not start service: {service}")
        self.service = service


class PulseStopError(PulseLifecycleError):
    def __init__(self, services: Sequence[str]) -> None:
        failed = tuple(services)
        super().__init__(f"Pulse could not stop services: {', '.join(failed)}")
        self.services = failed


class Pulse:
    """Start registered services in order and stop them in reverse order."""

    def __init__(self) -> None:
        self._state = PulseState.NEW
        self._services: list[PulseService] = []
        self._active: list[PulseService] = []
        self._events: list[PulseEvent] = []

    @property
    def state(self) -> PulseState:
        return self._state

    def register(self, service: PulseService) -> None:
        if self._state is not PulseState.NEW:
            raise PulseLifecycleError("Pulse services can only be registered before start.")

        name = str(service.name).strip()
        if not name:
            raise ValueError("Pulse service names cannot be empty.")
        if any(existing.name == name for existing in self._services):
            raise ValueError(f"Pulse service name is already registered: {name}")

        self._services.append(service)

    def snapshot(self) -> PulseSnapshot:
        return PulseSnapshot(
            state=self._state,
            registered=tuple(service.name for service in self._services),
            active=tuple(service.name for service in self._active),
            events=tuple(self._events),
        )

    def _record(self, service: str, phase: str, status: str) -> None:
        self._events.append(
            PulseEvent(
                sequence=len(self._events) + 1,
                service=service,
                phase=phase,
                status=status,
            )
        )

    async def start(self) -> PulseSnapshot:
        if self._state is PulseState.RUNNING:
            return self.snapshot()
        if self._state is not PulseState.NEW:
            raise PulseLifecycleError(f"Pulse cannot start from state: {self._state}")

        self._state = PulseState.STARTING

        for service in self._services:
            self._record(service.name, "start", "started")
            try:
                await service.start()
            except Exception as exc:
                self._record(service.name, "start", "failed")
                await self._rollback_start()
                self._state = PulseState.FAILED
                raise PulseStartError(service.name) from exc
            self._active.append(service)
            self._record(service.name, "start", "completed")

        self._state = PulseState.RUNNING
        return self.snapshot()

    async def _rollback_start(self) -> None:
        for service in reversed(self._active):
            self._record(service.name, "rollback", "started")
            try:
                await service.stop()
            except Exception:
                self._record(service.name, "rollback", "failed")
            else:
                self._record(service.name, "rollback", "completed")
        self._active.clear()

    async def stop(self) -> PulseSnapshot:
        if self._state is PulseState.STOPPED:
            return self.snapshot()
        if self._state is PulseState.NEW:
            self._state = PulseState.STOPPED
            return self.snapshot()
        if self._state is not PulseState.RUNNING:
            raise PulseLifecycleError(f"Pulse cannot stop from state: {self._state}")

        self._state = PulseState.STOPPING
        failures: list[str] = []

        for service in reversed(self._active):
            self._record(service.name, "stop", "started")
            try:
                await service.stop()
            except Exception:
                failures.append(service.name)
                self._record(service.name, "stop", "failed")
            else:
                self._record(service.name, "stop", "completed")

        self._active.clear()

        if failures:
            self._state = PulseState.FAILED
            raise PulseStopError(failures)

        self._state = PulseState.STOPPED
        return self.snapshot()


__all__ = [
    "Pulse",
    "PulseEvent",
    "PulseLifecycleError",
    "PulseService",
    "PulseSnapshot",
    "PulseStartError",
    "PulseState",
    "PulseStopError",
]
