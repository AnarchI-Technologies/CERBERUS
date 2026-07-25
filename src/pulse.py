import time
from src.switch_board.interface import ISwitchBoard
from src.events.types import LifecycleEvent, LifecycleStage

class Pulse:
    """
    Manages the primary runtime lifecycle and orchestrates other components
    via the Switch Board. It is completely game-agnostic.
    """
    def __init__(self, switch_board: ISwitchBoard):
        publish = getattr(switch_board, "publish", None)
        if not callable(publish):
            raise TypeError(
                "switch_board must provide a callable publish(event) capability."
            )
        self._switch_board = switch_board
        self._is_running = False

    def run(self, duration_seconds: float = -1):
        """
        Starts the main application loop, publishing lifecycle events.
        """
        try:
            self._is_running = True
            self._publish_lifecycle_event(LifecycleStage.STARTING)
            self._publish_lifecycle_event(LifecycleStage.RUNNING)
            start_time = time.time()
            
            while self._is_running:
                time.sleep(0.1)  # Main loop heartbeat
                if duration_seconds > 0 and (time.time() - start_time) > duration_seconds:
                    self.stop()
        except KeyboardInterrupt:
            print("\nINFO: Pulse received shutdown signal (Ctrl+C).")
        finally:
            if self._is_running:
                self.stop()
            print("INFO: Pulse has shut down cleanly.")

    def stop(self):
        if not self._is_running:
            return
        self._publish_lifecycle_event(LifecycleStage.STOPPING)
        self._is_running = False
        self._publish_lifecycle_event(LifecycleStage.STOPPED)

    def _publish_lifecycle_event(self, stage: LifecycleStage):
        event = LifecycleEvent(source="Pulse", stage=stage)
        print(f"PULSE: Publishing lifecycle event - {stage.name}")
        self._switch_board.publish(event)
