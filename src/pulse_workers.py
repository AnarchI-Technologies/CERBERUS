"""Pulse adapters for long-running asynchronous CERBERUS workers."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pulse import Pulse


WorkerRunner = Callable[[], Awaitable[None]]


class PulseWorkerError(RuntimeError):
    """Raised when a Pulse-managed worker cannot start or stop cleanly."""


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    name: str
    running: bool
    thread_alive: bool
    failed: bool


class AsyncWorkerService:
    """Run one endless coroutine in a cancellable, dedicated event-loop thread."""

    def __init__(
        self,
        name: str,
        runner: WorkerRunner,
        *,
        start_timeout: float = 5.0,
        stop_timeout: float = 5.0,
    ) -> None:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Pulse worker names cannot be empty.")
        if start_timeout <= 0 or stop_timeout <= 0:
            raise ValueError("Pulse worker timeouts must be positive.")

        self.name = normalized_name
        self._runner = runner
        self._start_timeout = start_timeout
        self._stop_timeout = stop_timeout
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None
        self._error: BaseException | None = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        thread = self._thread
        task = self._task
        return bool(
            thread is not None
            and thread.is_alive()
            and task is not None
            and not task.done()
        )

    def snapshot(self) -> WorkerSnapshot:
        thread = self._thread
        return WorkerSnapshot(
            name=self.name,
            running=self.is_running,
            thread_alive=bool(thread is not None and thread.is_alive()),
            failed=self._error is not None,
        )

    async def start(self) -> None:
        if self.is_running:
            return

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return

            self._ready.clear()
            self._error = None
            self._thread = threading.Thread(
                target=self._run_worker,
                name=f"pulse-{self.name}",
                daemon=True,
            )
            self._thread.start()

        ready = await asyncio.to_thread(
            self._ready.wait,
            self._start_timeout,
        )
        if not ready:
            await self.stop()
            raise PulseWorkerError(
                f"Pulse worker did not initialize in time: {self.name}"
            )
        if self._error is not None:
            raise PulseWorkerError(
                f"Pulse worker failed during initialization: {self.name}"
            ) from self._error

    def _run_worker(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        try:
            task = loop.create_task(
                self._runner(),
                name=f"pulse-{self.name}",
            )
            self._task = task
            self._ready.set()
            loop.run_until_complete(task)
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            pending = asyncio.all_tasks(loop)
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.close()
            self._task = None
            self._loop = None

    async def stop(self) -> None:
        thread = self._thread
        loop = self._loop
        task = self._task

        if thread is None:
            return

        if loop is not None and task is not None and not task.done():
            loop.call_soon_threadsafe(task.cancel)

        await asyncio.to_thread(thread.join, self._stop_timeout)

        if thread.is_alive():
            raise PulseWorkerError(
                f"Pulse worker did not stop in time: {self.name}"
            )

        self._thread = None
        error = self._error
        self._error = None
        if error is not None and not isinstance(error, asyncio.CancelledError):
            raise PulseWorkerError(
                f"Pulse worker exited with an error: {self.name}"
            ) from error


def build_runtime_pulse(
    *,
    claw_enabled: bool,
    claw_runner: WorkerRunner,
    moltstation_enabled: bool,
    moltstation_runner: WorkerRunner,
) -> Pulse:
    """Build the canonical CERBERUS worker lifecycle in stable order."""

    pulse = Pulse()
    if claw_enabled:
        pulse.register(AsyncWorkerService("claw-runtime", claw_runner))
    if moltstation_enabled:
        pulse.register(
            AsyncWorkerService("moltstation-runtime", moltstation_runner)
        )
    return pulse


__all__ = [
    "AsyncWorkerService",
    "PulseWorkerError",
    "WorkerRunner",
    "WorkerSnapshot",
    "build_runtime_pulse",
]
