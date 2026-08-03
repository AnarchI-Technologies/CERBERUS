from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from pulse import Pulse, PulseLifecycleError, PulseStartError, PulseState


class FakeService:
    def __init__(
        self,
        name: str,
        calls: list[str],
        *,
        fail_start: bool = False,
        fail_stop: bool = False,
    ) -> None:
        self.name = name
        self.calls = calls
        self.fail_start = fail_start
        self.fail_stop = fail_stop

    async def start(self) -> None:
        self.calls.append(f"start:{self.name}")
        if self.fail_start:
            raise RuntimeError(f"start failed: {self.name}")

    async def stop(self) -> None:
        self.calls.append(f"stop:{self.name}")
        if self.fail_stop:
            raise RuntimeError(f"stop failed: {self.name}")


class PulseLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_starts_in_registration_order_and_stops_in_reverse(self) -> None:
        calls: list[str] = []
        pulse = Pulse()
        pulse.register(FakeService("claw-runtime", calls))
        pulse.register(FakeService("moltstation-runtime", calls))

        started = await pulse.start()
        stopped = await pulse.stop()

        self.assertEqual(
            calls,
            [
                "start:claw-runtime",
                "start:moltstation-runtime",
                "stop:moltstation-runtime",
                "stop:claw-runtime",
            ],
        )
        self.assertEqual(started.state, PulseState.RUNNING)
        self.assertEqual(started.active, ("claw-runtime", "moltstation-runtime"))
        self.assertEqual(stopped.state, PulseState.STOPPED)
        self.assertEqual(stopped.active, ())

    async def test_start_failure_rolls_back_started_services(self) -> None:
        calls: list[str] = []
        pulse = Pulse()
        pulse.register(FakeService("first", calls))
        pulse.register(FakeService("broken", calls, fail_start=True))

        with self.assertRaises(PulseStartError) as raised:
            await pulse.start()

        self.assertEqual(raised.exception.service, "broken")
        self.assertEqual(calls, ["start:first", "start:broken", "stop:first"])
        self.assertEqual(pulse.state, PulseState.FAILED)
        self.assertEqual(pulse.snapshot().active, ())

    async def test_start_and_stop_are_idempotent_after_success(self) -> None:
        calls: list[str] = []
        pulse = Pulse()
        pulse.register(FakeService("worker", calls))

        first_start = await pulse.start()
        second_start = await pulse.start()
        first_stop = await pulse.stop()
        second_stop = await pulse.stop()

        self.assertEqual(calls, ["start:worker", "stop:worker"])
        self.assertEqual(first_start, second_start)
        self.assertEqual(first_stop, second_stop)

    async def test_empty_pulse_has_deterministic_lifecycle(self) -> None:
        pulse = Pulse()

        started = await pulse.start()
        stopped = await pulse.stop()

        self.assertEqual(started.registered, ())
        self.assertEqual(started.events, ())
        self.assertEqual(stopped.state, PulseState.STOPPED)

    def test_duplicate_service_names_are_rejected(self) -> None:
        pulse = Pulse()
        pulse.register(FakeService("runtime", []))

        with self.assertRaisesRegex(ValueError, "already registered"):
            pulse.register(FakeService("runtime", []))

    async def test_registration_is_closed_after_start(self) -> None:
        pulse = Pulse()
        await pulse.start()

        with self.assertRaisesRegex(PulseLifecycleError, "only be registered"):
            pulse.register(FakeService("late", []))


class PulseSwitchBoardCompatibilityTests(unittest.TestCase):
    def test_sync_heartbeat_publishes_the_legacy_lifecycle_contract(self) -> None:
        from src.events.types import LifecycleEvent, LifecycleStage

        switch_board = MagicMock()
        pulse = Pulse(switch_board=switch_board)
        thread = threading.Thread(target=lambda: pulse.run(duration_seconds=0.1), daemon=True)
        thread.start()
        thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        events = [call.args[0] for call in switch_board.publish.call_args_list]
        self.assertTrue(all(isinstance(event, LifecycleEvent) for event in events))
        self.assertEqual(
            [event.stage for event in events],
            [LifecycleStage.STARTING, LifecycleStage.RUNNING, LifecycleStage.STOPPING, LifecycleStage.STOPPED],
        )


if __name__ == "__main__":
    unittest.main()
