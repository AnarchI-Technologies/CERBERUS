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
    }
    for item in wisdom_entries("social", "voice", "runtime"):
        rows = item.get("social_policy", {})
        if not isinstance(rows, dict):
            continue
        for key in ("default_triggers", "preferred_submolts"):
            current = policy.get(key, {})
            incoming = rows.get(key, {})
            if isinstance(current, dict) and isinstance(incoming, dict):
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
