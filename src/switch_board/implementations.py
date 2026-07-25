from collections import defaultdict
from typing import Callable, Dict, List, Type
from src.events.types import BaseEvent
from src.switch_board.interface import ISwitchBoard

class InMemorySwitchBoard(ISwitchBoard):
    def __init__(self):
        self._subscribers: Dict[Type[BaseEvent], List[Callable[[BaseEvent], None]]] = defaultdict(list)

    def publish(self, event: BaseEvent) -> None:
        event_type = type(event)
        # Notify subscribers for the specific event type
        for handler in self._subscribers[event_type]:
            try:
                handler(event)
            except Exception as e:
                print(f"ERROR: Handler {getattr(handler, "__name__", handler.__class__.__name__)} failed for event {event_type.__name__}: {e}")
        
        # Also notify subscribers for any event (BaseEvent)
        if event_type is not BaseEvent:
            for handler in self._subscribers[BaseEvent]:
                try:
                    handler(event)
                except Exception as e:
                    print(f"ERROR: Base event handler {getattr(handler, "__name__", handler.__class__.__name__)} failed for event {event_type.__name__}: {e}")

    def subscribe(self, event_type: Type[BaseEvent], handler: Callable[[BaseEvent], None]) -> None:
        if not callable(handler):
            raise TypeError("Handler must be a callable function.")
        self._subscribers[event_type].append(handler)
        print(f"INFO: Handler {getattr(handler, "__name__", handler.__class__.__name__)} subscribed to {event_type.__name__}")
