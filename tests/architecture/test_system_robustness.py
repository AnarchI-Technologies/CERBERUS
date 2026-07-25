import time
import threading
from unittest.mock import MagicMock, Mock
import pytest

from src.pulse import Pulse
from src.switch_board.implementations import InMemorySwitchBoard
from src.events.types import GameStateUpdateEvent, LifecycleEvent, LifecycleStage


def test_switch_board_isolates_subscriber_failures():
    """
    Verifies that if one event handler fails, other handlers for the same
    event are still called. This proves the robustness of the event bus.
    """
    # Arrange
    switch_board = InMemorySwitchBoard()
    event = GameStateUpdateEvent(source="Test", game_id="g1", game_state={})

    failing_handler = MagicMock(side_effect=ValueError("Handler failed!"))
    successful_handler = MagicMock()

    switch_board.subscribe(GameStateUpdateEvent, failing_handler)
    switch_board.subscribe(GameStateUpdateEvent, successful_handler)

    # Act
    # This should not raise an exception, as the Switch Board should catch it.
    switch_board.publish(event)

    # Assert
    failing_handler.assert_called_once_with(event)
    successful_handler.assert_called_once_with(event)


def test_pulse_is_game_agnostic_and_can_run_any_adapter():
    """
    Proves the core architectural goal: Pulse is game-agnostic.
    We create a completely different mock adapter for a new game, "StarSailor",
    and verify that Pulse can run it without any changes.
    """
    # Arrange: Create a new, simple adapter for a different game
    class StarSailorAdapter:
        def __init__(self, switch_board):
            self._switch_board = switch_board
            self._is_active = False

        def start(self):
            self._switch_board.subscribe(LifecycleEvent, self._handle_lifecycle)

        def _handle_lifecycle(self, event: LifecycleEvent):
            if event.stage == LifecycleStage.RUNNING:
                self._is_active = True
                # On start, publish a unique game state event
                state_event = GameStateUpdateEvent(source="StarSailorAdapter", game_id="ss1", game_state={"ship_status": "All systems nominal."})
                self._switch_board.publish(state_event)
            elif event.stage == LifecycleStage.STOPPING:
                self._is_active = False

    switch_board = InMemorySwitchBoard()
    pulse = Pulse(switch_board=switch_board)
    starsailor_adapter = StarSailorAdapter(switch_board=switch_board)
    mock_consumer = MagicMock()

    # Act
    starsailor_adapter.start()
    switch_board.subscribe(GameStateUpdateEvent, mock_consumer)

    # Run Pulse for a short duration
    pulse_thread = threading.Thread(target=lambda: pulse.run(duration_seconds=0.5), daemon=True)
    pulse_thread.start()
    pulse_thread.join(timeout=1.0)

    # Assert
    # Was our mock consumer called with the StarSailor event?
    mock_consumer.assert_called_once()
    received_event = mock_consumer.call_args[0][0]
    assert isinstance(received_event, GameStateUpdateEvent)
    assert received_event.source == "StarSailorAdapter"
    assert received_event.game_state["ship_status"] == "All systems nominal."