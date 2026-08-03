import time
import threading
from unittest.mock import MagicMock, Mock
import pytest

from src.pulse import Pulse
from src.claw_royale.adapter import ClawRoyaleAdapter
from src.switch_board.implementations import InMemorySwitchBoard
from src.events.types import GameStateUpdateEvent

def test_pulse_and_adapter_integrate_correctly_via_switch_board():
    """
    Verifies that Pulse can start the Adapter and the Adapter can publish
    game state, all without either component knowing about the other directly.
    This is a key integration test for the decoupled architecture.
    """
    # Arrange
    switch_board = InMemorySwitchBoard()
    mock_game_api = Mock()
    mock_game_api.get_current_state.return_value = {"player_name": "CERBERUS"}

    # A mock consumer that represents our Strategy Engine
    mock_consumer = MagicMock()

    pulse = Pulse(switch_board=switch_board)
    adapter = ClawRoyaleAdapter(switch_board=switch_board, game_api_client=mock_game_api)

    # Act
    switch_board.subscribe(GameStateUpdateEvent, mock_consumer)
    adapter.start()

    # Run Pulse in a separate thread so we can stop it after a delay
    pulse_thread = threading.Thread(target=lambda: pulse.run(duration_seconds=1.5), daemon=True)
    pulse_thread.start()
    pulse_thread.join(timeout=2.0) # Wait for the pulse to finish

    # Assert
    mock_game_api.get_current_state.assert_called()
    mock_consumer.assert_called()
    received_event = mock_consumer.call_args[0][0]
    assert isinstance(received_event, GameStateUpdateEvent)
    assert received_event.source == "ClawRoyaleAdapter"
