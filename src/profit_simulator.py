"""Deterministic profit simulation for Hellion's Claw Royale policy.

The simulator is intentionally small and assumption-driven. It does not claim
live earnings; it answers a narrower question: given compact synthetic game
states, does the current decision policy choose the actions that move Hellion
toward a daily sMoltz target?
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_dossiers import AgentDossierStore
from core_loop import cerberus_tick
from memory_system import CompactMemoryStore


@dataclass(frozen=True, slots=True)
class ProfitScenario:
    name: str
    state: dict[str, Any]
    reward_by_action: dict[str, float]
    weight: float = 1.0


def baseline_scenarios() -> list[ProfitScenario]:
    return [
        ProfitScenario(
            name="free_smoltz_at_feet",
            weight=4.0,
            reward_by_action={"pickup:cash-1": 20.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 4, "inventory": []},
                    "currentRegion": {"id": "r1", "items": [{"id": "cash-1", "typeId": "smoltz_bundle"}]},
                },
            },
        ),
        ProfitScenario(
            name="contested_smoltz_at_feet",
            weight=2.0,
            reward_by_action={"pickup:cash-2": 20.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 4, "atk": 25, "inventory": [], "equippedWeapon": {"typeId": "katana"}},
                    "currentRegion": {"id": "r1", "items": [{"id": "cash-2", "typeId": "smoltz_bundle"}]},
                    "visibleAgents": [{"id": "rival-1", "hp": 20, "atk": 8, "def": 2}],
                },
            },
        ),
        ProfitScenario(
            name="weapon_before_guardian",
            weight=2.0,
            reward_by_action={"pickup:dagger-1": 20.0, "attack:guardian-1": -8.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 96, "ep": 4, "atk": 8, "inventory": [], "equippedWeapon": {"typeId": "fist"}},
                    "currentRegion": {"id": "r1", "items": [{"id": "dagger-1", "typeId": "dagger"}]},
                    "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 8, "def": 8}],
                },
            },
        ),
        ProfitScenario(
            name="killable_guardian",
            weight=3.0,
            reward_by_action={"attack:guardian-1": 20.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 4, "atk": 20, "inventory": [], "equippedWeapon": {"typeId": "katana"}},
                    "currentRegion": {"id": "r1"},
                    "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 70, "atk": 8, "def": 3}],
                    "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 24, "atk": 8, "def": 4}],
                },
            },
        ),
        ProfitScenario(
            name="bad_guardian_chip",
            weight=1.0,
            reward_by_action={"move:r2": 2.0, "attack:guardian-1": -8.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 4, "atk": 4, "inventory": [], "equippedWeapon": {"typeId": "fist"}},
                    "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                    "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 8, "def": 8}],
                },
            },
        ),
        ProfitScenario(
            name="heal_before_profitable_fight",
            weight=2.0,
            reward_by_action={"use_item:med-1": 10.0, "attack:rival-1": -12.0},
            state={
                "canAct": True,
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 42,
                        "maxHp": 100,
                        "ep": 4,
                        "atk": 24,
                        "inventory": [{"id": "med-1", "typeId": "medkit"}],
                        "equippedWeapon": {"typeId": "katana"},
                    },
                    "currentRegion": {"id": "r1"},
                    "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 22, "atk": 18, "def": 3}],
                },
            },
        ),
        ProfitScenario(
            name="deathzone_escape_with_loot",
            weight=3.0,
            reward_by_action={"move:safe-1": 18.0, "pickup:cash-3": -20.0, "rest": -30.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 74, "ep": 3, "inventory": [{"id": "relic-1", "typeId": "relic_red"}]},
                    "currentRegion": {
                        "id": "danger-1",
                        "isDeathZone": True,
                        "items": [{"id": "cash-3", "typeId": "smoltz_bundle"}],
                        "connections": [{"id": "safe-1"}],
                    },
                    "visibleRegions": [{"id": "safe-1", "terrain": "Plain"}],
                },
            },
        ),
        ProfitScenario(
            name="safe_ruin_rotation",
            weight=2.0,
            reward_by_action={"move:ruin-1": 14.0, "explore": 8.0},
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 4, "inventory": []},
                    "currentRegion": {"id": "r1", "connections": [{"id": "ruin-1", "terrain": "Ruin"}]},
                    "visibleRegions": [{"id": "ruin-1", "terrain": "Ruin", "name": "Old Vault"}],
                },
            },
        ),
    ]


def action_key(action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "unknown")
    target = action.get("itemId") or action.get("targetId") or action.get("regionId") or ""
    return f"{action_type}:{target}" if target else action_type


def decide_for_scenario(scenario: ProfitScenario) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        memory = CompactMemoryStore(
            path=Path(tmp) / "memory.compact.json",
            encrypted_path=Path(tmp) / "memory.compact.vault.json",
        ).load()
        dossiers = AgentDossierStore(
            path=Path(tmp) / "agent_dossiers.compact.json",
            encrypted_path=Path(tmp) / "agent_dossiers.compact.vault.json",
        ).load()
        return cerberus_tick(scenario.state, memory_store=memory, dossier_store=dossiers)


def simulate(*, games_per_day: int = 50, target_per_day: float = 1000.0) -> dict[str, Any]:
    scenarios = baseline_scenarios()
    rows = []
    weighted_total = 0.0
    weight_sum = 0.0
    for scenario in scenarios:
        action = decide_for_scenario(scenario)
        key = action_key(action)
        reward = scenario.reward_by_action.get(key, 0.0)
        best_key, best_reward = max(
            scenario.reward_by_action.items(),
            key=lambda item: item[1],
            default=("", 0.0),
        )
        weighted_total += reward * scenario.weight
        weight_sum += scenario.weight
        rows.append(
            {
                "scenario": scenario.name,
                "action": key,
                "reward": reward,
                "best_action": best_key,
                "best_reward": best_reward,
                "weight": scenario.weight,
                "reason": action.get("reason", ""),
                "missed_best": reward < best_reward,
            }
        )

    expected_per_game = weighted_total / max(1.0, weight_sum)
    expected_per_day = expected_per_game * games_per_day
    required_games = int((target_per_day + expected_per_game - 1) // expected_per_game) if expected_per_game > 0 else 0
    return {
        "assumptions": {
            "games_per_day": games_per_day,
            "target_per_day": target_per_day,
            "guardian_reward": 20,
            "smoltz_bundle_reward": 20,
            "synthetic": True,
        },
        "expected_smoltz_per_game": round(expected_per_game, 3),
        "expected_smoltz_per_day": round(expected_per_day, 3),
        "target_met": expected_per_day >= target_per_day,
        "required_games_for_target": required_games,
        "gap_smoltz_per_day": round(max(0.0, target_per_day - expected_per_day), 3),
        "policy_gaps": [
            {
                "scenario": row["scenario"],
                "action": row["action"],
                "reason": row["reason"],
                "best_action": row["best_action"],
                "best_reward": row["best_reward"],
            }
            for row in rows
            if row["missed_best"]
        ],
        "scenarios": rows,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Run Hellion deterministic profit simulation")
    parser.add_argument("--games-per-day", type=int, default=50)
    parser.add_argument("--target-per-day", type=float, default=1000.0)
    args = parser.parse_args()
    print(json.dumps(simulate(games_per_day=args.games_per_day, target_per_day=args.target_per_day), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
