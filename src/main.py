"""
The main entrypoint for the CERBERUS application.

This script is responsible for the "composition root" of the system. It
instantiates all major components, injects their dependencies, and starts
the primary runtime loop.
"""

import time
from unittest.mock import Mock

# Core System Components
from src.pulse import Pulse
from src.switch_board.implementations import InMemorySwitchBoard
from src.events.types import GameStateUpdateEvent, ActionRequestEvent

# Game-Specific Components
from src.claw_royale.adapter import ClawRoyaleAdapter

def main():
    """
    Assembles and runs the complete event-driven application.
    """
    print("==================================================")
    print("CERBERUS Autonomous Game Platform: Initializing...")
    print("==================================================")

    # 1. Instantiate Core Services
    switch_board = InMemorySwitchBoard()

    # 2. Instantiate and Inject Dependencies
    # The runtime doesn't know about games, only the event bus.
    pulse = Pulse(switch_board=switch_board)

    # The adapter needs a client to talk to the actual game API.
    # For this test run, we'll mock it.
    mock_game_api_client = Mock()
    mock_game_api_client.get_current_state.return_value = {
        "player_name": "CERBERUS",
        "resources": {"minerals": 1000, "relics": 3},
        "active_quests": ["q_hunt_wolf"],
    }

    claw_royale_adapter = ClawRoyaleAdapter(
        switch_board=switch_board,
        game_api_client=mock_game_api_client
    )

    # This is our mock "Strategy Engine". It's just a function that
    # listens for game state and decides to publish an action.
    def strategy_engine_handler(event: GameStateUpdateEvent):
        if hasattr(event, 'game_state'):
            print(f"\nSTRATEGY ENGINE: Received game state update from {event.source}.")
            print(f"  -> State: {event.game_state}")

            # Decide on an action
            action_to_take = {"type": "build", "unit": "Guardian"}
            action_event = ActionRequestEvent(
                source="StrategyEngine",
                game_id=event.game_id,
                strategy_id="basic_build_order",
                action=action_to_take
            )
            print(f"STRATEGY ENGINE: Publishing action request: {action_to_take}")
            switch_board.publish(action_event)

    # 3. Subscribe and Start Services
    print("\nCOMPOSITION: Subscribing components to the Switch Board...")
    # The Strategy Engine listens for game state.
    switch_board.subscribe(GameStateUpdateEvent, strategy_engine_handler)
    # The Adapter needs to be started to listen for lifecycle events.
    claw_royale_adapter.start()

    # 4. Run the Application
    print("\nRUNTIME: Starting Pulse. Press Ctrl+C to shut down.")
    try:
        # This will block and run the main loop until stopped.
        pulse.run()
    except Exception as e:
        print(f"\nFATAL ERROR: An unhandled exception occurred in the main runtime: {e}")
    finally:
        print("\nCERBERUS: Shutdown complete.")


if __name__ == "__main__":
    main()
