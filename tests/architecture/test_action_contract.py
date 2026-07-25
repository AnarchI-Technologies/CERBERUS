import time
import threading
from unittest.mock import MagicMock, Mock
from src.pulse import Pulse
from src.claw_royale.adapter import ClawRoyaleAdapter
from src.switch_board.implementations import InMemorySwitchBoard
from src.events.types import GameStateUpdateEvent, ActionRequestEvent

def test_full_action_round_trip():
    """
    Verifies the full "Action Round-Trip" contract:
    1. Adapter publishes state.
    2. Strategy Engine receives state and publishes an action request.
    3. Adapter receives the action request and executes it.
    """
    # Arrange
    switch_board = InMemorySwitchBoard()
    mock_game_api = Mock()
    mock_game_api.get_current_state.return_value = {"turn": 1}

    pulse = Pulse(switch_board=switch_board)
    adapter = ClawRoyaleAdapter(switch_board=switch_board, game_api_client=mock_game_api)

    def mock_strategy_engine(event: GameStateUpdateEvent):
        """A mock engine that immediately requests an action."""
        action_event = ActionRequestEvent(
            source="StrategyEngine",
            game_id=event.game_id,
            strategy_id="test_strategy",
            action={"type": "attack", "target": "enemy_1"}
        )
        switch_board.publish(action_event)

    # Act
    switch_board.subscribe(GameStateUpdateEvent, mock_strategy_engine)
    adapter.start()

    pulse_thread = threading.Thread(target=lambda: pulse.run(duration_seconds=1.5), daemon=True)
    pulse_thread.start()
    pulse_thread.join(timeout=2.0)

    # Assert
    # Was the game API polled for state?
    mock_game_api.get_current_state.assert_called()
    # Was the action executed on the game API?
    mock_game_api.execute_action.assert_called_once_with(
        {"type": "attack", "target": "enemy_1"}
    )
