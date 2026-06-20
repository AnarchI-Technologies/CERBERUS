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

from memory_system import DEFAULT_MEMORY_DIR, atomic_write_text, scrub_scalar, stable_hash, utc_now
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
    helpful_messages: int = 0
    truthful_messages: int = 0
    alliance_offers: int = 0
    alliance_score: int = 0
    betrayed_us: int = 0
    betrayed_them: int = 0
    communication_notes: list[str] = field(default_factory=list)
    validated_handoffs: int = 0
    failed_handoffs: int = 0
    evidence_counts: dict[str, int] = field(default_factory=dict)
    last_handoff_packet: dict[str, Any] = field(default_factory=dict)

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
        if self.alliance_score:
            bits.append(f"ally={self.alliance_score}")
        if self.helpful_messages:
            bits.append(f"help={self.helpful_messages}")
        if self.betrayed_us or self.betrayed_them:
            bits.append(f"betray={self.betrayed_us}/{self.betrayed_them}")
        if self.validated_handoffs or self.failed_handoffs:
            bits.append(f"handoff={self.validated_handoffs}/{self.failed_handoffs}")
        if self.evidence_counts:
            ranked = sorted(self.evidence_counts.items(), key=lambda item: (-int(item[1]), str(item[0])))
            bits.append("anchors=" + ",".join(f"{key}:{value}" for key, value in ranked[:3]))
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
        atomic_write_text(
            self.path,
            json.dumps(data, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
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

    def observe_agent_profile(
        self,
        agent_id: str,
        *,
        name: str = "",
        tendency: str = "",
        handle: str = "",
        followed: bool = False,
    ) -> AgentDossier:
        record = self.observe_agent(agent_id, name=name, tendency=tendency)
        if handle:
            normalized = str(handle).strip()
            if normalized:
                record.moltybook_handle = normalized
        if followed:
            record.followed = True
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
        return self.observe_agent_profile(agent_id, handle=handle, followed=followed)

    def social_handle_for(self, agent_id: str) -> str:
        record = self.records.get(agent_id)
        if record is None:
            return ""
        return str(record.moltybook_handle or "")

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

    def record_helpful_message(
        self,
        agent_id: str,
        *,
        name: str = "",
        note: str = "",
        truthful: bool = False,
        alliance_offer: bool = False,
    ) -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        record.helpful_messages += 1
        record.alliance_score += 2 + (1 if truthful else 0) + (1 if alliance_offer else 0)
        if truthful:
            record.truthful_messages += 1
        if alliance_offer:
            record.alliance_offers += 1
        if note:
            self.add_communication_note(agent_id, note)
        self.records[agent_id] = record
        return record

    def record_betrayal_by_them(self, agent_id: str, *, name: str = "", note: str = "") -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        record.betrayed_us += 1
        record.alliance_score = max(-12, record.alliance_score - 6)
        if note:
            self.add_communication_note(agent_id, note)
        self.records[agent_id] = record
        return record

    def record_betrayal_by_us(self, agent_id: str, *, name: str = "", note: str = "") -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        record.betrayed_them += 1
        if note:
            self.add_communication_note(agent_id, note)
        self.records[agent_id] = record
        return record

    def add_communication_note(self, agent_id: str, note: str) -> None:
        record = self.records.get(agent_id) or AgentDossier(agent_id=agent_id)
        compact = note[:180]
        if compact not in record.communication_notes:
            record.communication_notes.append(compact)
            record.communication_notes = record.communication_notes[-16:]
        self.records[agent_id] = record

    def record_evidence_anchor(
        self,
        agent_id: str,
        anchor: str,
        *,
        name: str = "",
        note: str = "",
    ) -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        key = scrub_scalar(anchor or "opponent_claim", limit=24) or "opponent_claim"
        counts = dict(record.evidence_counts or {})
        counts[key] = int(counts.get(key) or 0) + 1
        record.evidence_counts = counts
        if note:
            self.add_communication_note(agent_id, note)
        self.records[agent_id] = record
        return record

    def record_handoff_packet(
        self,
        agent_id: str,
        packet: dict[str, Any],
        *,
        name: str = "",
        validated: bool = False,
        failed: bool = False,
    ) -> AgentDossier:
        record = self.observe_agent(agent_id, name=name)
        anchor = scrub_scalar(packet.get("evidence_anchor") or "opponent_claim", limit=24) or "opponent_claim"
        self.record_evidence_anchor(agent_id, anchor, name=name)
        compact_packet = {
            "intent": scrub_scalar(packet.get("intent"), limit=32),
            "claim": scrub_scalar(packet.get("claim"), limit=120),
            "evidence_anchor": anchor,
            "requested_action": scrub_scalar(packet.get("requested_action"), limit=40),
            "uncertainty": scrub_scalar(packet.get("uncertainty"), limit=24),
        }
        record = self.records.get(agent_id) or record
        record.last_handoff_packet = compact_packet
        if validated:
            record.validated_handoffs += 1
            record.alliance_score += 1
        if failed:
            record.failed_handoffs += 1
            record.alliance_score = max(-12, record.alliance_score - 2)
        self.add_communication_note(
            agent_id,
            f"packet:{compact_packet.get('intent','comment')}:{anchor}:{compact_packet.get('requested_action','log_only')}",
        )
        self.records[agent_id] = record
        return record

    def compact_context(self, limit: int = 24) -> str:
        records = sorted(self.records.values(), key=lambda record: record.last_seen, reverse=True)
        return "\n".join(record.compact() for record in records[:limit])

    def add_tendency(self, record: AgentDossier, tendency: str) -> None:
        text = tendency[:80]
        if text not in record.observed_tendencies:
            record.observed_tendencies.append(text)
            record.observed_tendencies = record.observed_tendencies[-12:]
