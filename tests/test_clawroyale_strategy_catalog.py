from __future__ import annotations

import ast
import sys
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from clawroyale_ai import STRATEGY_BY_ID, STRATEGY_CATALOG
from compatibility import LegacyStrategyRegistry, StrategyCallCache, StrategyRegistryError


@dataclass
class FakeResult:
    intent: str


@dataclass
class FakeProvider:
    intents: tuple[str, ...]
    calls: int = 0

    def evaluate(self, state, context):
        self.calls += 1
        return [FakeResult(intent) for intent in self.intents]


def empty_state(**overrides):
    values = {
        "inventory": [],
        "visible_items": [],
        "visible_agents": [],
        "visible_monsters": [],
        "events": [],
        "recent_messages": [],
        "current_region": SimpleNamespace(items=[]),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class StrategyCatalogTests(unittest.TestCase):
    def test_every_strategy_has_a_unique_stable_id(self) -> None:
        self.assertEqual(len(STRATEGY_CATALOG), 54)
        self.assertEqual(len(STRATEGY_BY_ID), 54)
        self.assertTrue(
            all(item.strategy_id == f"clawroyale.{item.intent}" for item in STRATEGY_CATALOG)
        )

    def test_catalog_matches_all_literal_legacy_intents(self) -> None:
        source_root = next(
            path
            for entry in sys.path
            if (path := Path(entry)).joinpath("threat_engine.py").is_file()
            and path.name == "src"
        )
        repository = source_root.parent
        provider_paths = {
            item.provider: (
                repository / "data" / f"{item.provider}.py"
                if item.provider == "memory_cortex"
                else repository / "src" / f"{item.provider}.py"
            )
            for item in STRATEGY_CATALOG
        }
        discovered: set[tuple[str, str]] = set()
        for provider, path in provider_paths.items():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and node.arg == "intent":
                    discovered.update(
                        (provider, child.value)
                        for child in ast.walk(node.value)
                        if isinstance(child, ast.Constant)
                        and isinstance(child.value, str)
                    )
        catalogued = {(item.provider, item.intent) for item in STRATEGY_CATALOG}
        self.assertEqual(catalogued, discovered)

    def test_each_strategy_is_directly_callable_by_id(self) -> None:
        provider = FakeProvider(("escape_death_zone", "heal_low_hp"))
        registry = LegacyStrategyRegistry({"threat_engine": provider})
        result = registry.call(
            "clawroyale.heal_low_hp",
            empty_state(),
            {},
        )
        self.assertEqual(tuple(item.intent for item in result), ("heal_low_hp",))

    def test_calls_share_one_provider_evaluation(self) -> None:
        provider = FakeProvider(("escape_death_zone", "heal_low_hp"))
        registry = LegacyStrategyRegistry({"threat_engine": provider})
        cache = StrategyCallCache()
        registry.call("clawroyale.escape_death_zone", empty_state(), {}, cache=cache)
        registry.call("clawroyale.heal_low_hp", empty_state(), {}, cache=cache)
        self.assertEqual(provider.calls, 1)

    def test_fast_gate_skips_owner_provider_without_messages(self) -> None:
        provider = FakeProvider(("owner_requested_rest",))
        registry = LegacyStrategyRegistry({"owner_command_cortex": provider})
        result = registry.call(
            "clawroyale.owner_requested_rest",
            empty_state(),
            {"owner_messages": []},
        )
        self.assertEqual(result, ())
        self.assertEqual(provider.calls, 0)

    def test_fast_gate_enables_owner_provider_with_messages(self) -> None:
        provider = FakeProvider(("owner_requested_rest",))
        registry = LegacyStrategyRegistry({"owner_command_cortex": provider})
        result = registry.call(
            "clawroyale.owner_requested_rest",
            empty_state(),
            {"owner_messages": [{"text": "rest"}]},
        )
        self.assertEqual(tuple(item.intent for item in result), ("owner_requested_rest",))
        self.assertEqual(provider.calls, 1)

    def test_evaluate_all_runs_each_eligible_provider_once(self) -> None:
        threat = FakeProvider(("escape_death_zone", "heal_low_hp"))
        owner = FakeProvider(("owner_requested_rest",))
        registry = LegacyStrategyRegistry(
            {"threat_engine": threat, "owner_command_cortex": owner}
        )
        evaluation = registry.evaluate_all(empty_state(), {})
        self.assertEqual(threat.calls, 1)
        self.assertEqual(owner.calls, 0)
        self.assertIn("threat_engine", evaluation.providers_evaluated)
        self.assertNotIn("owner_command_cortex", evaluation.providers_evaluated)

    def test_unknown_strategy_fails_closed(self) -> None:
        registry = LegacyStrategyRegistry({})
        with self.assertRaises(StrategyRegistryError):
            registry.call("clawroyale.missing", empty_state(), {})

    def test_independent_catalog_has_no_legacy_imports(self) -> None:
        source = (ROOT / "src" / "clawroyale_ai" / "catalog.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("core_loop", source)
        self.assertNotIn("Cortex", source)
        self.assertNotIn("TurnState", source)


if __name__ == "__main__":
    unittest.main()
