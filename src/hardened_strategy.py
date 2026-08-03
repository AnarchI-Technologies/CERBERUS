"""Generated deterministic strategy rules for runtime hardening."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from memory_system import utc_now


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_FILE = ROOT / "data" / "hardened_strategy_rules.json"
RULES_VERSION = 1
DEFAULT_RULES = {
    "heal_hp_ratio_floor": 0.68,
    "known_killer_hp_ratio_floor": 0.72,
    "observed_finisher_hp_ratio_floor": 0.70,
    "repeat_prey_min_kills": 1,
    "social_autodrain_on_enqueue": True,
    "postgame_memory_rescan_before_loadout": True,
    "cross_agent_dossier_learning": True,
    "prefer_failure_memory_weighting": True,
    "consistent_public_persona": True,
    "bounded_public_persona": True,
    "external_wisdom_validation_required": True,
    "heartbeat_lightweight_only": True,
    "postgame_batch_window": True,
    "social_post_requires_trigger": True,
    "social_focus_submolts": True,
    "layered_memory_hierarchy": True,
}


def default_rules() -> dict[str, Any]:
    return {
        "type": "cerberus.hardened_strategy_rules",
        "version": RULES_VERSION,
        "updated_at": utc_now(),
        "rules": dict(DEFAULT_RULES),
        "hardened_from": {"lessons": [], "suggested_edits": []},
    }


def hardened_strategy_rules_file() -> Path:
    runtime_root = os.getenv("CERBERUS_MEMORY_DIR") or os.getenv("CERBERUS_HOME")
    return Path(runtime_root) / DEFAULT_RULES_FILE.name if runtime_root else DEFAULT_RULES_FILE


def load_hardened_strategy_rules(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else hardened_strategy_rules_file()
    try:
        if target.exists():
            data = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                rules = data.get("rules")
                if not isinstance(rules, dict):
                    data["rules"] = dict(DEFAULT_RULES)
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return default_rules()


def save_hardened_strategy_rules(payload: dict[str, Any], path: str | Path | None = None) -> Path:
    target = Path(path) if path is not None else hardened_strategy_rules_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
    return target


def sanity_check_rules(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"ok": False, "error": "rules_not_dict"}
    rules = payload.get("rules")
    if not isinstance(rules, dict):
        return {"ok": False, "error": "missing_rules"}
    for key in ("heal_hp_ratio_floor", "known_killer_hp_ratio_floor", "observed_finisher_hp_ratio_floor"):
        value = rules.get(key)
        if not isinstance(value, (int, float)) or value < 0.2 or value > 0.95:
            return {"ok": False, "error": f"invalid_threshold:{key}"}
    repeat_prey_min_kills = rules.get("repeat_prey_min_kills")
    if not isinstance(repeat_prey_min_kills, int) or repeat_prey_min_kills < 1 or repeat_prey_min_kills > 4:
        return {"ok": False, "error": "invalid_repeat_prey_min_kills"}
    if not isinstance(rules.get("social_autodrain_on_enqueue"), bool):
        return {"ok": False, "error": "invalid_social_autodrain_on_enqueue"}
    if not isinstance(rules.get("postgame_memory_rescan_before_loadout"), bool):
        return {"ok": False, "error": "invalid_postgame_memory_rescan_before_loadout"}
    for key in (
        "cross_agent_dossier_learning",
        "prefer_failure_memory_weighting",
        "consistent_public_persona",
        "bounded_public_persona",
        "external_wisdom_validation_required",
        "heartbeat_lightweight_only",
        "postgame_batch_window",
        "social_post_requires_trigger",
        "social_focus_submolts",
        "layered_memory_hierarchy",
    ):
        if not isinstance(rules.get(key), bool):
            return {"ok": False, "error": f"invalid_{key}"}
    return {"ok": True}
