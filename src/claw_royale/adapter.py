import hashlib
import json
import threading
import time
from typing import Any

from src.events.types import (
    ActionRequestEvent,
    GameStateUpdateEvent,
    LifecycleEvent,
    LifecycleStage,
)
from src.switch_board.interface import ISwitchBoard


class ClawRoyaleAdapter:
    """
    Connects to the Claw Royale game.

    The adapter listens for lifecycle events, publishes changed game-state
    snapshots, and executes action requests addressed to its active game.
    """

    def __init__(
        self,
        switch_board: ISwitchBoard,
        game_api_client: Any,
    ):
        self._switch_board = switch_board
        self._game_api_client = game_api_client
        self._is_active = False
        self.game_id = f"cr_match_{int(time.time())}"
        self._last_published_game_id: str | None = None
        self._last_state_signature: str | None = None

    def start(self) -> None:
        """Subscribe the adapter to the required system events."""
        print("ADAPTER: Subscribing to system events.")
        self._switch_board.subscribe(
            LifecycleEvent,
            self._handle_lifecycle_event,
        )
        self._switch_board.subscribe(
            ActionRequestEvent,
            self._handle_action_request,
        )

    def _handle_lifecycle_event(
        self,
        event: LifecycleEvent,
    ) -> None:
        """Start or stop polling according to Pulse lifecycle state."""
        if (
            event.stage == LifecycleStage.RUNNING
            and not self._is_active
        ):
            print(
                "ADAPTER: Received RUNNING signal. "
                "Starting main polling loop."
            )
            self._is_active = True

            threading.Thread(
                target=self._main_loop,
                daemon=True,
            ).start()

        elif event.stage in (
            LifecycleStage.STOPPING,
            LifecycleStage.STOPPED,
        ):
            if self._is_active:
                print(
                    "ADAPTER: Received STOP signal. "
                    "Shutting down polling loop."
                )
                self._is_active = False

    def _handle_action_request(
        self,
        event: ActionRequestEvent,
    ) -> None:
        """
        Execute an action request addressed to this adapter's active game.
        """
        if event.game_id != self.game_id:
            return

        print(
            "ADAPTER: Received action request from strategy "
            f"'{event.strategy_id}'."
        )

        self._game_api_client.execute_action(event.action)

        print(f"  -> Executed action: {event.action}")

    @staticmethod
    def _state_signature(
        game_id: str,
        game_state: Any,
    ) -> str:
        """
        Return a deterministic, game-scoped signature for a state snapshot.

        Including the game ID ensures identical snapshots from separate games
        are treated as distinct events.
        """
        canonical_state = json.dumps(
            game_state,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

        payload = f"{game_id}\n{canonical_state}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _publish_state_if_changed(
        self,
        raw_state: Any,
    ) -> bool:
        """
        Publish the current game state only when its signature has changed.

        Returns True when an event was published and False when the snapshot
        was an unchanged duplicate.
        """
        state_signature = self._state_signature(
            self.game_id,
            raw_state,
        )

        is_duplicate = (
            self._last_published_game_id == self.game_id
            and self._last_state_signature == state_signature
        )

        if is_duplicate:
            return False

        state_event = GameStateUpdateEvent(
            source="ClawRoyaleAdapter",
            game_id=self.game_id,
            game_state=raw_state,
        )

        self._switch_board.publish(state_event)

        self._last_published_game_id = self.game_id
        self._last_state_signature = state_signature
        return True

    def _main_loop(self) -> None:
        """Poll the game and publish only changed state snapshots."""
        while self._is_active:
            try:
                raw_state = self._game_api_client.get_current_state()
                self._publish_state_if_changed(raw_state)
                time.sleep(1.0)

            except Exception as error:
                print(
                    "ERROR: ClawRoyaleAdapter main loop failed: "
                    f"{error}"
                )
                time.sleep(5.0)