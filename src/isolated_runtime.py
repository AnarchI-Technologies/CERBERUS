"""
Isolated Cerberus runtime for stress tests and sandboxed hardening runs.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_dossiers import AgentDossierStore
from core_loop import cerberus_tick
from memory_system import CompactMemoryStore
from runtime_state import overridden_memory_dir


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

    @contextmanager
    def runtime_env(self):
        with overridden_memory_dir(self.root):
            yield

    def tick(self, state: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        with self.runtime_env():
            return cerberus_tick(state, memory_store=self.memory, dossier_store=self.dossiers, **kwargs)

    def reload(self) -> "IsolatedCerberusInstance":
        return IsolatedCerberusInstance.create(self.root)
