"""
Isolated Cerberus runtime for stress tests and sandboxed hardening runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_dossiers import AgentDossierStore
from core_loop import cerberus_tick
from memory_system import CompactMemoryStore


@dataclass(slots=True)
class IsolatedCerberusInstance:
    root: Path
    memory: CompactMemoryStore
    dossiers: AgentDossierStore

    @classmethod
    def create(cls, root: str | Path) -> "IsolatedCerberusInstance":
        base = Path(root)
        base.mkdir(parents=True, exist_ok=True)
        memory = CompactMemoryStore(
            path=base / "memory.compact.json",
            encrypted_path=base / "memory.compact.vault.json",
        ).load()
        dossiers = AgentDossierStore(
            path=base / "agent_dossiers.compact.json",
            encrypted_path=base / "agent_dossiers.compact.vault.json",
        ).load()
        return cls(root=base, memory=memory, dossiers=dossiers)

    def tick(self, state: dict[str, Any]) -> dict[str, Any]:
        return cerberus_tick(state, memory_store=self.memory, dossier_store=self.dossiers)

    def reload(self) -> "IsolatedCerberusInstance":
        return IsolatedCerberusInstance.create(self.root)
