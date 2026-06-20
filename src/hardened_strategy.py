"""Generated deterministic strategy rules for runtime hardening."""

from __future__ import annotations

import json
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
}


def default_rules() -> dict[str, Any]:
    return {
        "type": "cerberus.hardened_strategy_rules",
        "version": RULES_VERSION,
        "updated_at": utc_now(),
        "rules": dict(DEFAULT_RULES),
        "hardened_from": {"lessons": [], "suggested_edits": []},
    }


def load_hardened_strategy_rules(path: str | Path = DEFAULT_RULES_FILE) -> dict[str, Any]:
    target = Path(path)
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


def save_hardened_strategy_rules(payload: dict[str, Any], path: str | Path = DEFAULT_RULES_FILE) -> Path:
    target = Path(path)
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
    return {"ok": True}
