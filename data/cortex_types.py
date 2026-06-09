"""
Shared contracts for Cerberus cortex modules.

Each cortex emits scored intents. The Arbiter selects a single primary game
action and can also return non-blocking side effects such as MoltyBook drafts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


Action = dict[str, Any]


@dataclass(slots=True)
class CortexResult:
    cortex: str
    intent: str
    score: float
    risk: float = 0.0
    priority: int = 50
    action: Action | None = None
    reason: str = ""
    veto: bool = False
    side_effects: list[dict[str, Any]] = field(default_factory=list)
    source_facts: list[str] = field(default_factory=list)

    @property
    def utility(self) -> float:
        return self.score - self.risk + self.priority / 100.0

    def action_type(self) -> str:
        if not self.action:
            return ""
        return str(self.action.get("type") or self.action.get("data", {}).get("type") or "")

    def to_plan_entry(self) -> dict[str, Any]:
        return {
            "cortex": self.cortex,
            "intent": self.intent,
            "score": round(self.score, 3),
            "risk": round(self.risk, 3),
            "priority": self.priority,
            "utility": round(self.utility, 3),
            "action": self.action,
            "reason": self.reason,
            "veto": self.veto,
            "side_effects": self.side_effects,
            "source_facts": self.source_facts,
        }


class Cortex(Protocol):
    name: str

    def evaluate(self, state: Any, context: dict[str, Any]) -> list[CortexResult]:
        ...


def action(action_type: str, **data: Any) -> Action:
    return {"type": action_type, **{k: v for k, v in data.items() if v is not None}}


def rest_action(reason: str = "hold position and recover") -> Action:
    return action("rest", reason=reason)
