from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

import core_loop
from agent_dossiers import AgentDossierStore
from compatibility import LegacyStrategyRegistry, build_legacy_strategy_providers
from decision_engine import make_plan
from memory_system import CompactMemoryStore


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


def decision_snapshot():
    return {
        "gameId": "registry-execution",
        "turn": 11,
        "canAct": True,
        "view": {
            "self": {
                "id": "hellion",
                "hp": 78,
                "maxHp": 100,
                "ep": 6,
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
            "aliveCount": 20,
        },
    }


class StrategyRegistryExecutionTests(unittest.TestCase):
    def test_decision_engine_accepts_registry_and_reports_prefilter_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            memory, dossiers = stores(Path(directory), "registry")
            registry = LegacyStrategyRegistry(
                build_legacy_strategy_providers(
                    memory_store=memory,
                    dossier_store=dossiers,
                )
            )
            plan = make_plan(
                state=decision_snapshot(),
                memory_store=memory,
                dossier_store=dossiers,
                strategy_registry=registry,
            )

        self.assertIsNotNone(plan["strategy_registry"])
        self.assertGreater(plan["strategy_registry"]["strategies_skipped"], 0)
        self.assertNotIn(
            "owner_command_cortex",
            plan["strategy_registry"]["providers_evaluated"],
        )

    def test_decision_engine_rejects_ambiguous_execution_inputs(self):
        with self.assertRaisesRegex(ValueError, "cortexes or strategy_registry"):
            make_plan(
                state=decision_snapshot(),
                cortexes=[object()],
                strategy_registry=object(),
            )

    def test_core_defaults_to_registry_execution(self):
        calls = []
        original = LegacyStrategyRegistry.evaluate_all

        def tracked(registry, state, context):
            calls.append(tuple(registry.strategy_ids()))
            return original(registry, state, context)

        with tempfile.TemporaryDirectory() as directory:
            memory, dossiers = stores(Path(directory), "default")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(core_loop.STRATEGY_EXECUTION_ENV, None)
                with mock.patch.object(
                    core_loop.LegacyStrategyRegistry,
                    "evaluate_all",
                    new=tracked,
                ):
                    core_loop.cerberus_tick(
                        decision_snapshot(),
                        memory_store=memory,
                        dossier_store=dossiers,
                    )

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 54)

    def test_legacy_fallback_matches_registry_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            registry_memory, registry_dossiers = stores(root, "registry")
            legacy_memory, legacy_dossiers = stores(root, "legacy")
            with mock.patch.dict(
                os.environ,
                {core_loop.STRATEGY_EXECUTION_ENV: "registry"},
            ):
                registry_action = core_loop.cerberus_tick(
                    decision_snapshot(),
                    memory_store=registry_memory,
                    dossier_store=registry_dossiers,
                )
            with mock.patch.dict(
                os.environ,
                {core_loop.STRATEGY_EXECUTION_ENV: "legacy"},
            ):
                legacy_action = core_loop.cerberus_tick(
                    decision_snapshot(),
                    memory_store=legacy_memory,
                    dossier_store=legacy_dossiers,
                )

        self.assertEqual(registry_action, legacy_action)

    def test_invalid_execution_mode_fails_closed_to_registry(self):
        with mock.patch.dict(
            os.environ,
            {core_loop.STRATEGY_EXECUTION_ENV: "surprise"},
        ):
            self.assertEqual(core_loop.strategy_execution_mode(), "registry")


if __name__ == "__main__":
    unittest.main()
