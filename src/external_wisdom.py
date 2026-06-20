"""Curated external wisdom rendered into deterministic compiler input."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LIBRARY_FILE = ROOT / "data" / "external_wisdom_library.json"


def load_external_wisdom_library(path: str | Path = DEFAULT_LIBRARY_FILE) -> dict[str, Any]:
    target = Path(path)
    try:
        if target.exists():
            payload = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {"type": "cerberus.external_wisdom_library", "version": 1, "entries": []}


def wisdom_entry(key: str, path: str | Path = DEFAULT_LIBRARY_FILE) -> dict[str, Any]:
    wanted = str(key or "").strip()
    if not wanted:
        return {}
    for item in wisdom_entries(path=path):
        if str(item.get("key") or "") == wanted:
            return item
    return {}


def wisdom_entries(*domains: str, path: str | Path = DEFAULT_LIBRARY_FILE) -> list[dict[str, Any]]:
    payload = load_external_wisdom_library(path)
    entries = payload.get("entries", [])
    if not isinstance(entries, list):
        return []
    cleaned = [item for item in entries if isinstance(item, dict) and item.get("key")]
    if not domains:
        return cleaned
    wanted = {str(item).strip().lower() for item in domains if item}
    out: list[dict[str, Any]] = []
    for item in cleaned:
        item_domains = item.get("domains", [])
        if not isinstance(item_domains, list):
            continue
        lowered = {str(domain).strip().lower() for domain in item_domains if domain}
        if lowered.intersection(wanted):
            out.append(item)
    return out


def validated_strategy_wisdom(path: str | Path = DEFAULT_LIBRARY_FILE) -> list[dict[str, Any]]:
    from strategy_validation import StrategyValidator

    validator = StrategyValidator()
    accepted: list[dict[str, Any]] = []
    for item in wisdom_entries(path=path):
        claim = str(item.get("strategy_claim") or "")
        if not claim:
            continue
        verdict = validator.validate(claim)
        if verdict.accepted:
            accepted.append({**item, "validation": verdict})
    return accepted


def shared_public_line(kind: str, fallback: str) -> str:
    normalized = str(kind or "").strip().lower()
    for item in wisdom_entries("voice", "thoughts", "social", "owner_command"):
        lines = item.get("shared_public_lines", {})
        if not isinstance(lines, dict):
            continue
        candidate = str(lines.get(normalized) or lines.get("default") or "").strip()
        if candidate:
            return candidate
    return fallback


def voice_guardrails() -> list[str]:
    out: list[str] = []
    for item in wisdom_entries("voice", "thoughts", "social", "owner_command"):
        rows = item.get("guardrails", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            text = str(row).strip()
            if text and text not in out:
                out.append(text)
    return out


def voice_soundbites() -> list[str]:
    out: list[str] = []
    for item in wisdom_entries("voice", "thoughts", "social"):
        rows = item.get("soundbites", [])
        if not isinstance(rows, list):
            continue
        for row in rows:
            text = str(row).strip()
            if text and text not in out:
                out.append(text)
    return out


def scheduler_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {
        "heartbeat_lightweight_only": False,
        "postgame_batch_window": True,
        "heartbeat_budget_ms": 0,
        "heartbeat_allowed_work": [],
        "heartbeat_blocked_work": [],
        "postgame_order": [],
        "autodrain_triggers": [],
        "deferred_triggers": [],
    }
    for item in wisdom_entries("runtime", "hardening", "social"):
        rows = item.get("scheduler_policy", {})
        if not isinstance(rows, dict):
            continue
        for key, value in rows.items():
            if isinstance(value, list):
                merged = list(policy.get(key, [])) if isinstance(policy.get(key), list) else []
                for entry in value:
                    text = str(entry).strip()
                    if text and text not in merged:
                        merged.append(text)
                policy[key] = merged
            else:
                policy[key] = value
    return policy


def memory_hierarchy_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {}
    for item in wisdom_entries("memory", "lessons", "hardening"):
        rows = item.get("memory_policy", {})
        if isinstance(rows, dict):
            policy.update(rows)
    return policy


def social_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {
        "default_triggers": {},
        "preferred_submolts": {},
        "category_tones": {},
        "posting_prerequisites": {},
        "tagging_rules": {},
    }
    for item in wisdom_entries("social", "voice", "runtime"):
        rows = item.get("social_policy", {})
        if not isinstance(rows, dict):
            continue
        for key in ("default_triggers", "preferred_submolts", "category_tones", "posting_prerequisites", "tagging_rules"):
            current = policy.get(key, {})
            incoming = rows.get(key, {})
            if isinstance(current, dict) and isinstance(incoming, dict):
                if key == "posting_prerequisites":
                    current.update(
                        {
                            str(k): [str(item).strip() for item in v if str(item).strip()]
                            for k, v in incoming.items()
                            if k and isinstance(v, list)
                        }
                    )
                elif key == "tagging_rules":
                    current.update({str(k): bool(v) for k, v in incoming.items() if k})
                else:
                    current.update({str(k): str(v) for k, v in incoming.items() if k and v})
                policy[key] = current
    return policy


def social_trigger_for(category: str, fallback: str = "maintenance") -> str:
    triggers = social_policy().get("default_triggers", {})
    if isinstance(triggers, dict):
        return str(triggers.get(category) or fallback)
    return fallback


def preferred_submolt_for(category: str, fallback: str = "") -> str:
    submolts = social_policy().get("preferred_submolts", {})
    if isinstance(submolts, dict):
        return str(submolts.get(category) or fallback)
    return fallback


def social_tone_for(category: str, fallback: str = "bounded_public_persona") -> str:
    tones = social_policy().get("category_tones", {})
    if isinstance(tones, dict):
        return str(tones.get(category) or fallback)
    return fallback


def posting_prerequisites_for(category: str) -> list[str]:
    prerequisites = social_policy().get("posting_prerequisites", {})
    if isinstance(prerequisites, dict):
        rows = prerequisites.get(category, [])
        if isinstance(rows, list):
            return [str(item).strip() for item in rows if str(item).strip()]
    return []


def tagging_rules() -> dict[str, bool]:
    rows = social_policy().get("tagging_rules", {})
    if isinstance(rows, dict):
        return {str(key): bool(value) for key, value in rows.items() if key}
    return {}


def _merged_policy(*policy_names: str, domains: tuple[str, ...] = ()) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    items = wisdom_entries(*domains) if domains else wisdom_entries()
    for item in items:
        for policy_name in policy_names:
            block = item.get(policy_name, {})
            if isinstance(block, dict):
                merged.update(block)
    return merged


def idempotency_policy() -> dict[str, Any]:
    return _merged_policy("idempotency_policy", domains=("runtime", "social", "memory", "decision"))


def retrieval_policy() -> dict[str, Any]:
    return _merged_policy("retrieval_policy", domains=("memory", "decision", "hardening"))


def memory_decay_policy() -> dict[str, Any]:
    return _merged_policy("memory_decay_policy", domains=("memory", "lessons", "hardening"))


def storage_policy() -> dict[str, Any]:
    return _merged_policy("storage_policy", domains=("memory", "hardening", "owner_command"))


def decision_review_policy() -> dict[str, Any]:
    return _merged_policy("decision_review_policy", domains=("decision", "lessons", "memory"))


def contract_policy() -> dict[str, Any]:
    return _merged_policy("contract_policy", domains=("runtime", "decision", "hardening", "owner_command"))


def tool_trust_policy() -> dict[str, Any]:
    return _merged_policy("tool_trust_policy", domains=("owner_command", "runtime", "hardening", "memory"))


def scout_policy() -> dict[str, Any]:
    return _merged_policy("scout_policy", domains=("runtime", "hardening", "memory", "social"))


def confirmation_policy() -> dict[str, Any]:
    return _merged_policy("confirmation_policy", domains=("runtime", "decision", "social", "hardening"))


def state_machine_policy() -> dict[str, Any]:
    return _merged_policy("state_machine_policy", domains=("runtime", "hardening", "owner_command"))


def opportunity_cost_policy() -> dict[str, Any]:
    return _merged_policy("opportunity_cost_policy", domains=("decision", "runtime", "social", "lessons"))


def resumability_policy() -> dict[str, Any]:
    return _merged_policy("resumability_policy", domains=("runtime", "memory", "hardening"))


def execution_boundary_policy() -> dict[str, Any]:
    return _merged_policy("execution_boundary_policy", domains=("hardening", "runtime", "memory"))


def verification_policy() -> dict[str, Any]:
    return _merged_policy("verification_policy", domains=("hardening", "runtime", "decision"))


def transport_confirmation_policy() -> dict[str, Any]:
    return _merged_policy("transport_confirmation_policy", domains=("runtime", "social", "hardening"))


def authority_policy() -> dict[str, Any]:
    return _merged_policy("authority_policy", domains=("owner_command", "runtime", "social", "hardening"))


def privileged_tool_policy() -> dict[str, Any]:
    return _merged_policy("privileged_tool_policy", domains=("owner_command", "runtime", "hardening"))


def handoff_policy() -> dict[str, Any]:
    return _merged_policy("handoff_policy", domains=("social", "runtime", "owner_command", "memory"))


def handoff_packet_policy() -> dict[str, Any]:
    return _merged_policy("handoff_packet_policy", domains=("social", "runtime", "memory", "owner_command"))


def evidential_policy() -> dict[str, Any]:
    return _merged_policy("evidential_policy", domains=("social", "runtime", "memory", "decision"))


def divergence_policy() -> dict[str, Any]:
    return _merged_policy("divergence_policy", domains=("hardening", "runtime", "social", "owner_command"))


def contraction_policy() -> dict[str, Any]:
    return _merged_policy("contraction_policy", domains=("social", "owner_command", "runtime", "voice"))


def invariant_guard_policy() -> dict[str, Any]:
    return _merged_policy("invariant_guard_policy", domains=("hardening", "runtime", "owner_command"))


def topology_policy() -> dict[str, Any]:
    return _merged_policy("topology_policy", domains=("runtime", "decision", "hardening", "social"))


def anomaly_fuse_policy() -> dict[str, Any]:
    return _merged_policy("anomaly_fuse_policy", domains=("runtime", "hardening", "owner_command"))


def truthfulness_policy() -> dict[str, Any]:
    return _merged_policy("truthfulness_policy", domains=("social", "owner_command", "runtime"))


def trust_chain_policy() -> dict[str, Any]:
    return _merged_policy("trust_chain_policy", domains=("social", "runtime", "owner_command", "hardening"))
