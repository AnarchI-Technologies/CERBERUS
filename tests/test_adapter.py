import time
from unittest.mock import MagicMock, Mock
import pytest

from src.claw_royale.adapter import ClawRoyaleAdapter
from src.events.types import LifecycleEvent, LifecycleStage, GameStateUpdateEvent, ActionRequestEvent


@pytest.fixture
def adapter_components():
    """Pytest fixture to set up adapter and its mocks."""
    mock_switch_board = MagicMock()
    mock_game_api = Mock()
    adapter = ClawRoyaleAdapter(
        switch_board=mock_switch_board,
        game_api_client=mock_game_api
    )
    return adapter, mock_switch_board, mock_game_api


def test_start_subscribes_to_events(adapter_components):
    """
    Verifies that the adapter subscribes to the correct events when started.
    """
    adapter, mock_switch_board, _ = adapter_components
    # Act
    adapter.start()

    # Assert
    subscribe_calls = mock_switch_board.subscribe.call_args_list
    assert len(subscribe_calls) == 2
    assert subscribe_calls[0].args[0] == LifecycleEvent
    assert subscribe_calls[1].args[0] == ActionRequestEvent


def test_handle_lifecycle_event_starts_polling_on_running(adapter_components):
    """
    Verifies the adapter starts its internal polling loop when it receives
    a RUNNING lifecycle event.
    """
    adapter, mock_switch_board, mock_game_api = adapter_components
    # Arrange
    mock_game_api.get_current_state.return_value = {"status": "ok"}
    
    # Act
    adapter.start()
    # Manually call the handler as the switch board would
    adapter._handle_lifecycle_event(LifecycleEvent(source="Pulse", stage=LifecycleStage.RUNNING))
    time.sleep(0.1)  # Give the thread time to start and run once
    adapter._handle_lifecycle_event(LifecycleEvent(source="Pulse", stage=LifecycleStage.STOPPING))

    # Assert
    mock_game_api.get_current_state.assert_called()
    mock_switch_board.publish.assert_called()
    published_event = mock_switch_board.publish.call_args[0][0]
    assert isinstance(published_event, GameStateUpdateEvent)


def test_handle_action_request_executes_action(adapter_components):
    """
    Verifies the adapter calls the game API when it receives an ActionRequestEvent.
    """
    adapter, _, mock_game_api = adapter_components
    action_event = ActionRequestEvent(source="StrategyEngine", game_id=adapter.game_id, strategy_id="test", action={"type": "build"})
    adapter._handle_action_request(action_event)
    mock_game_api.execute_action.assert_called_once_with({"type": "build"})

def test_identical_state_is_published_once():
    switch_board = MagicMock()
    game_api_client = MagicMock()

    adapter = ClawRoyaleAdapter(
        switch_board=switch_board,
        game_api_client=game_api_client,
    )

    state = {"turn": 1}

    first_result = adapter._publish_state_if_changed(state)
    second_result = adapter._publish_state_if_changed(state)

    assert first_result is True
    assert second_result is False
    assert switch_board.publish.call_count == 1


def test_changed_state_is_published_again():
    switch_board = MagicMock()
    game_api_client = MagicMock()

    adapter = ClawRoyaleAdapter(
        switch_board=switch_board,
        game_api_client=game_api_client,
    )

    assert adapter._publish_state_if_changed({"turn": 1}) is True
    assert adapter._publish_state_if_changed({"turn": 2}) is True
    assert switch_board.publish.call_count == 2


def test_same_state_in_new_game_is_published_again():
    switch_board = MagicMock()
    game_api_client = MagicMock()

    adapter = ClawRoyaleAdapter(
        switch_board=switch_board,
        game_api_client=game_api_client,
    )

    state = {"turn": 1}

    assert adapter._publish_state_if_changed(state) is True

    adapter.game_id = "cr_match_new"

    assert adapter._publish_state_if_changed(state) is True
    assert switch_board.publish.call_count == 2