from abc import ABC, abstractmethod
from typing import Callable, Type
from src.events.types import BaseEvent

class ISwitchBoard(ABC):
    @abstractmethod
    def publish(self, event: BaseEvent) -> None:
        raise NotImplementedError

    @abstractmethod
    def subscribe(self, event_type: Type[BaseEvent], handler: Callable[[BaseEvent], None]) -> None:
        raise NotImplementedError
