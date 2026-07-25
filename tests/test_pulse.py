import time
import threading
from unittest.mock import MagicMock, call

from src.pulse import Pulse
from src.events.types import LifecycleEvent, LifecycleStage


def test_pulse_publishes_correct_lifecycle_events():
    """
    Verifies that Pulse publishes the correct sequence of lifecycle events
    (STARTING, RUNNING, STOPPING, STOPPED) via the Switch Board.
    """
    # Arrange
    mock_switch_board = MagicMock()
    pulse = Pulse(switch_board=mock_switch_board)

    # Act
    # Run Pulse in a separate thread so this test doesn't block forever.
    # We'll tell it to run for a very short duration.
    pulse_thread = threading.Thread(target=lambda: pulse.run(duration_seconds=0.2), daemon=True)
    pulse_thread.start()
    pulse_thread.join(timeout=1.0)  # Wait for the thread to complete

    # Assert
    assert not pulse_thread.is_alive(), "Pulse thread should have terminated."

    # Check that the switch board's publish method was called with the correct events
    # in the correct order.
    calls = mock_switch_board.publish.call_args_list
    assert len(calls) == 4
    assert all(isinstance(c.args[0], LifecycleEvent) for c in calls)
    assert [c.args[0].stage for c in calls] == [LifecycleStage.STARTING, LifecycleStage.RUNNING, LifecycleStage.STOPPING, LifecycleStage.STOPPED]