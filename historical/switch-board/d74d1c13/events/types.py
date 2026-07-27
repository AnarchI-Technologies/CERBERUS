from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

@dataclass(frozen=True)
class BaseEvent:
    event_id: UUID = field(default_factory=uuid4, init=False)
    timestamp_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc), init=False)
    source: str

class LifecycleStage(Enum):
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"

@dataclass(frozen=True)
class LifecycleEvent(BaseEvent):
    stage: LifecycleStage

@dataclass(frozen=True)
class GameStateUpdateEvent(BaseEvent):
    game_id: str
    game_state: Any

@dataclass(frozen=True)
class ActionRequestEvent(BaseEvent):
    game_id: str
    strategy_id: str
    action: Dict[str, Any]

# We can add other specific event types here as needed.
