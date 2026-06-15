"""
Compact battlefield + MoltyBook dossiers for encountered agents.

These records are agent-facing memory, not public posts. They should help the
cortexes recognize repeat opponents without preserving raw logs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory_system import DEFAULT_MEMORY_DIR, stable_hash, utc_now
from secret_vault import read_vault, write_vault


DEFAULT_DOSSIER_FILE = DEFAULT_MEMORY_DIR / "agent_dossiers.compact.json"
DEFAULT_DOSSIER_VAULT = DEFAULT_MEMORY_DIR / "agent_dossiers.compact.vault.json"


@dataclass(slots=True)
class AgentDossier:
    agent_id: str
    name: str = ""
    first_seen: str = field(default_factory=utc_now)
    last_seen: str = field(default_factory=utc_now)
    encounter_count: int = 0
    killed_by_us: int = 0
    killed_us: int = 0
    tricked_into_death: int = 0
    observed_tendencies: list[str] = field(default_factory=list)
    moltybook_handle: str = ""
    followed: bool = False
    social_notes: list[str] = field(default_factory=list)
    validated_strats: list[str] = field(default_factory=list)

    def compact(self) -> str:
        bits = [
            f"id={self.agent_id}",
            f"name={self.name}",
            f"seen={self.encounter_count}",
            f"killed={self.killed_by_us}",
            f"killed_us={self.killed_us}",
            f"tricked={self.tricked_into_death}",
        ]
        if self.moltybook_handle:
            bits.append(f"mb={self.moltybook_handle}")
        if self.followed:
            bits.append("followed=true")
        if self.observed_tendencies:
            bits.append("tend=" + ",".join(self.observed_tendencies[-5:]))
        if self.validated_strats:
            bits.append("strats=" + ",".join(self.validated_strats[-3:]))
        if self.social_notes:
            bits.append("notes=" + ",".join(self.social_notes[-2:]))
        return "D|" + ";".join(bits)


class AgentDossierStore:
    def __init__(
        self,
        *,
        path: str | Path = DEFAULT_DOSSIER_FILE,
        encrypted_path: str | Path = DEFAULT_DOSSIER_VAULT,
    ):
        self.path = Path(path)
        self.encrypted_path = Path(encrypted_path)
        self.records: dict[str, AgentDossier] = {}

    def load(self) -> "AgentDossierStore":
        try:
            if self.encrypted_path.exists():
                data = read_vault(self.encrypted_path)
            elif self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                data = {"records": {}}
        except Exception as exc:
            data = {"records": {}, "load_warning": str(exc)[:240]}
        fields = set(AgentDossier.__dataclass_fields__)
        self.records = {}
        for agent_id, record in data.get("records", {}).items():
            if not isinstance(record, dict):
                continue
            values = {key: value for key, value in record.items() if key in fields}
            values.setdefault("agent_id", str(agent_id))
            try:
                self.records[str(agent_id)] = AgentDossier(**values)
            except TypeError:
                continue
        return self

    def save(self, *, encrypt: bool = False) -> Path:
        data = {
            "type": "cerberus.agent_dossiers",
            "updated_at": utc_now(),
            "records": {
                agent_id: {
                    field: getattr(record, field)
                    for field in AgentDossier.__dataclass_fields__
                }
                for agent_id, record in self.records.items()
            },
        }
        if encrypt:
            return write_vault(self.encrypted_path, data, purpose="cerberus.agent_dossiers")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
        return self.path

    def observe_agent(self, agent_id: str, *, name: str = "", tendency: str = "") -> AgentDossier:
        record = self.records.get(agent_id) or AgentDossier(agent_id=agent_id)
        record.name = name or record.name
        record.last_seen = utc_now()
        record.encounter_count += 1
        if tendency:
            self.add_tendency(record, tendency)
        self.records[agent_id] = record
        return record

    def record_kill(self, agent_id: str, *, name: str = "", tricked: bool = False) -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        record.killed_by_us += 1
        if tricked:
            record.tricked_into_death += 1
        return record

    def record_killed_us(self, agent_id: str, *, name: str = "") -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        record.killed_us += 1
        return record

    def record_social_profile(self, agent_id: str, *, handle: str, followed: bool = False) -> AgentDossier:
        record = self.records.get(agent_id) or AgentDossier(agent_id=agent_id)
        record.moltybook_handle = handle
        record.followed = record.followed or followed
        record.last_seen = utc_now()
        self.records[agent_id] = record
        return record

    def add_social_note(self, agent_id: str, note: str) -> None:
        record = self.records.get(agent_id) or AgentDossier(agent_id=agent_id)
        compact = note[:180]
        if compact not in record.social_notes:
            record.social_notes.append(compact)
            record.social_notes = record.social_notes[-16:]
        self.records[agent_id] = record

    def add_validated_strategy(self, agent_id: str, text: str) -> str:
        record = self.records.get(agent_id) or AgentDossier(agent_id=agent_id)
        marker = f"{stable_hash(text, length=10)}:{text[:120]}"
        if marker not in record.validated_strats:
            record.validated_strats.append(marker)
            record.validated_strats = record.validated_strats[-12:]
        self.records[agent_id] = record
        return marker

    def compact_context(self, limit: int = 24) -> str:
        records = sorted(self.records.values(), key=lambda record: record.last_seen, reverse=True)
        return "\n".join(record.compact() for record in records[:limit])

    def add_tendency(self, record: AgentDossier, tendency: str) -> None:
        text = tendency[:80]
        if text not in record.observed_tendencies:
            record.observed_tendencies.append(text)
            record.observed_tendencies = record.observed_tendencies[-12:]
