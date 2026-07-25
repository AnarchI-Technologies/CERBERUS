from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List

@dataclass(frozen=True)
class Unit:
    id: str
    health: int
    max_health: int
    damage: int
    is_ranged: bool
    position: tuple[int, int]

@dataclass(frozen=True)
class CombatContext:
    friendly_units: List[Unit]
    enemy_units: List[Unit]
    can_retreat: bool
    tactical_advantage: float

class QuestType(Enum):
    GATHER = "gather"
    HUNT = "hunt"
    ESCORT = "escort"
    DELIVER = "deliver"

@dataclass(frozen=True)
class Quest:
    id: str
    type: QuestType
    objectives: List[Dict[str, Any]] = field(default_factory=list)
    rewards: List[Dict[str, Any]] = field(default_factory=list)
    is_complete: bool = False

class MessageType(Enum):
    GREETING = "greeting"
    TRADE_REQUEST = "trade_request"
    THREAT = "threat"
    UNKNOWN = "unknown"

@dataclass(frozen=True)
class MessageEvent:
    sender_id: str
    sender_reputation: float
    message_type: MessageType
    raw_text: str

@dataclass
class StrategyPerformanceProfile:
    strategy_id: str
    context_hash: str
    total_executions: int = 0
    success_count: int = 0
    failure_count: int = 0
    avg_net_resource_change: float = 0.0
    catastrophic_failure_count: int = 0
    last_updated_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_utc: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def success_rate(self) -> float:
        if self.total_executions == 0: return 0.5
        return self.success_count / self.total_executions

    @property
    def risk_factor(self) -> float:
        if self.total_executions == 0: return 0.0
        return self.catastrophic_failure_count / self.total_executions
