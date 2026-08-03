from __future__ import annotations

import asyncio
import sys
import threading
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pulse import PulseState
from pulse_workers import AsyncWorkerService, build_runtime_pulse


class AsyncWorkerServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_and_cancels_worker_thread(self) -> None:
        entered = threading.Event()
        cancelled = threading.Event()

        async def runner() -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        service = AsyncWorkerService("worker", runner)

        await service.start()
        self.assertTrue(entered.wait(1))
        self.assertTrue(service.snapshot().running)

        await service.stop()

        self.assertTrue(cancelled.wait(1))
        self.assertFalse(service.snapshot().thread_alive)

    async def test_start_and_stop_are_idempotent(self) -> None:
        starts = 0

        async def runner() -> None:
            nonlocal starts
            starts += 1
            await asyncio.Event().wait()

        service = AsyncWorkerService("worker", runner)

        await service.start()
        await service.start()
        await service.stop()
        await service.stop()

        self.assertEqual(starts, 1)

    async def test_runtime_pulse_registers_enabled_workers_in_stable_order(self) -> None:
        async def runner() -> None:
            await asyncio.Event().wait()

        pulse = build_runtime_pulse(
            claw_enabled=True,
            claw_runner=runner,
            moltstation_enabled=True,
            moltstation_runner=runner,
        )

        self.assertEqual(
            pulse.snapshot().registered,
            ("claw-runtime", "moltstation-runtime"),
        )

        started = await pulse.start()
        stopped = await pulse.stop()

        self.assertEqual(started.state, PulseState.RUNNING)
        self.assertEqual(stopped.state, PulseState.STOPPED)

    def test_runtime_pulse_omits_disabled_workers(self) -> None:
        async def runner() -> None:
            return None

        pulse = build_runtime_pulse(
            claw_enabled=False,
            claw_runner=runner,
            moltstation_enabled=True,
            moltstation_runner=runner,
        )

        self.assertEqual(
            pulse.snapshot().registered,
            ("moltstation-runtime",),
        )


if __name__ == "__main__":
    unittest.main()
