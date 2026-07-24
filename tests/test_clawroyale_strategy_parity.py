from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from agent_dossiers import AgentDossierStore
from compatibility import (
    LEGACY_PROVIDER_ORDER,
    build_legacy_strategy_providers,
    compare_legacy_strategy_registry,
)
from memory_system import CompactMemoryStore
from turn_state_model import TurnState


def stores(root: Path, name: str):
    memory = CompactMemoryStore(
        path=root / f"{name}-memory.json",
        encrypted_path=root / f"{name}-memory.vault",
    ).load()
    dossiers = AgentDossierStore(
        path=root / f"{name}-dossiers.json",
        encrypted_path=root / f"{name}-dossiers.vault",
    ).load()
    return memory, dossiers


def snapshot(**view_overrides):
    view = {
        "self": {
            "id": "hellion",
            "name": "Hellion",
            "hp": 75,
            "maxHp": 100,
            "ep": 8,
            "maxEp": 10,
            "regionId": "r1",
            "inventory": [],
        },
        "currentRegion": {
            "id": "r1",
            "name": "Ash Plains",
            "terrain": "plains",
            "connections": [{"id": "r2", "name": "Old Ruin", "terrain": "ruin"}],
            "items": [],
        },
        "visibleAgents": [],
        "visibleMonsters": [],
        "visibleItems": [],
        "recentMessages": [],
        "events": [],
        "aliveCount": 20,
        "canAct": True,
    }
    view.update(view_overrides)
    return {"gameId": "parity", "turn": 7, "view": view}


class RealStrategyRegistryParityTests(unittest.TestCase):
    def compare(self, raw_snapshot, *, owner_messages=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline_memory, baseline_dossiers = stores(root, "baseline")
            registry_memory, registry_dossiers = stores(root, "registry")
            context = {
                "threats": [],
                "opportunities": [],
                "memory": "",
                "owner_messages": owner_messages or [],
                "knowledge": None,
            }
            return compare_legacy_strategy_registry(
                TurnState.from_snapshot(raw_snapshot),
                context,
                baseline_providers=build_legacy_strategy_providers(
                    memory_store=baseline_memory,
                    dossier_store=baseline_dossiers,
                ),
                registry_providers=build_legacy_strategy_providers(
                    memory_store=registry_memory,
                    dossier_store=registry_dossiers,
                ),
            )

    def assert_parity(self, raw_snapshot, *, owner_messages=None):
        report = self.compare(raw_snapshot, owner_messages=owner_messages)
        self.assertTrue(report.passed, report.mismatches)
        self.assertEqual(report.mismatches, ())
        self.assertEqual(report.baseline_candidates, report.registry_candidates)
        self.assertEqual(report.baseline_action, report.registry_action)
        self.assertEqual(report.baseline_winner, report.registry_winner)
        return report

    def test_builder_preserves_current_core_order(self):
        with tempfile.TemporaryDirectory() as directory:
            memory, dossiers = stores(Path(directory), "order")
            providers = build_legacy_strategy_providers(
                memory_store=memory,
                dossier_store=dossiers,
            )
        self.assertEqual(tuple(providers), LEGACY_PROVIDER_ORDER)

    def test_minimal_snapshot_has_exact_parity_and_skips_idle_families(self):
        report = self.assert_parity(snapshot())
        self.assertGreater(report.strategies_skipped, 0)
        self.assertNotIn("owner_command_cortex", report.providers_evaluated)
        self.assertNotIn("combat_decider", report.providers_evaluated)

    def test_owner_command_snapshot_has_exact_parity(self):
        report = self.assert_parity(
            snapshot(),
            owner_messages=[{"text": "rest and recover", "sender": "owner"}],
        )
        self.assertIn("owner_command_cortex", report.providers_evaluated)

    def test_inventory_and_ground_items_have_exact_parity(self):
        raw = snapshot(
            self={
                "id": "hellion",
                "hp": 45,
                "maxHp": 100,
                "ep": 2,
                "maxEp": 10,
                "regionId": "r1",
                "inventory": [
                    {"id": "energy-1", "typeId": "energy_drink"},
                    {"id": "blade-1", "typeId": "iron_sword", "attack": 40},
                ],
            },
            visibleItems=[{"id": "moltz-1", "typeId": "moltz"}],
        )
        report = self.assert_parity(raw)
        self.assertIn("free_action_abuse", report.providers_evaluated)
        self.assertIn("utility_cortex", report.providers_evaluated)
        self.assertIn("ep_economy_engine", report.providers_evaluated)

    def test_visible_enemy_and_social_inputs_have_exact_parity(self):
        raw = snapshot(
            visibleAgents=[
                {
                    "id": "rival-1",
                    "name": "Rival",
                    "hp": 20,
                    "ep": 1,
                    "regionId": "r1",
                    "moltybookHandle": "rival",
                }
            ],
            recentMessages=[{"agentId": "rival-1", "text": "truce?"}],
            events=[{"type": "agent_attacked", "agentId": "rival-1"}],
        )
        report = self.assert_parity(raw)
        self.assertIn("combat_decider", report.providers_evaluated)
        self.assertIn("social_cortex", report.providers_evaluated)

    def test_low_health_danger_snapshot_has_exact_parity(self):
        raw = snapshot(
            self={
                "id": "hellion",
                "hp": 12,
                "maxHp": 100,
                "ep": 4,
                "maxEp": 10,
                "regionId": "r1",
                "inventory": [],
            },
            currentRegion={
                "id": "r1",
                "name": "Collapsing Sector",
                "terrain": "plains",
                "isDeathZone": True,
                "connections": [{"id": "r2", "name": "Safe Hill", "terrain": "hill"}],
                "items": [],
            },
        )
        self.assert_parity(raw)

    def test_sanitized_fixture_has_exact_parity(self):
        fixture = json.loads(
            (ROOT / "tests" / "fixtures" / "claw_agent_view_sanitized.json").read_text(
                encoding="utf-8"
            )
        )
        self.assert_parity(fixture)


if __name__ == "__main__":
    unittest.main()
