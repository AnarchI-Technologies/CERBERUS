"""
Memory Cortex: compact turn lessons and opponent dossiers.
"""

from __future__ import annotations

from agent_dossiers import AgentDossierStore
from cortex_types import CortexResult
from memory_system import CompactMemoryStore
from turn_state_model import TurnState


class MemoryCortex:
    name = "memory"

    def __init__(
        self,
        *,
        memory_store: CompactMemoryStore | None = None,
        dossier_store: AgentDossierStore | None = None,
    ):
        self.memory = memory_store
        self.dossiers = dossier_store or AgentDossierStore().load()

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        for agent in state.visible_agents:
            if agent.id and agent.id != state.self.id:
                tendency = ""
                if agent.hp <= 35:
                    tendency = "low_hp_when_seen"
                elif agent.ep <= 2:
                    tendency = "low_ep_when_seen"
                self.dossiers.observe_agent(agent.id, name=agent.name, tendency=tendency)

        return [
            CortexResult(
                cortex=self.name,
                intent="update_compact_memory",
                score=5,
                risk=0,
                priority=5,
                action=None,
                reason="memory/dossier observations updated",
                side_effects=[{"type": "memory_updated"}],
                source_facts=["F|memory.policy"],
            )
        ]
