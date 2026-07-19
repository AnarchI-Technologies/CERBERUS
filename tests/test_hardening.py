from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_dossiers import AgentDossierStore
from core_loop import cerberus_tick, normalize_action
from core_loop import legalize_action
import identity_bootstrap
import claw_contract
import claw_identity_token
import claw_config
import claw_runtime
import claw_signing
import cross_spinal_cord
import autonomy_suggestions
import stream_dashboard_cortex
import moltbook_claim_assistant
import render_app
import x_oauth
import runtime_state
import render_env_export
import env_doctor
import game_map
import launch_doctor
import env_loader
import secret_env_admin
import external_wisdom
import lesson_compiler
import memory_system
import mongo_memory
import profit_simulator
import owner_command_cortex
import social_runtime
import loadout_shop_reforge
import social_worker
import postgame_hardening
from identity_bootstrap import (
    ensure_agentmail,
    ensure_claw_account,
    ensure_moltbook,
    ensure_molty_wallet,
    ensure_twitch_account,
    ensure_wallets,
    ensure_wallet_roles,
    identity_public_name,
    moltbook_name,
    BootstrapResult,
)
from isolated_runtime import IsolatedCerberusInstance
from identity_vault import DEFAULT_PUBLIC_NAME, DEFAULT_V2_PUBLIC_NAME, empty_identity
from knowledge_base import KnowledgeBase
from ep_economy_engine import EconomyCortex
from free_action_abuse import FreeActionCortex
from longterm_memory import LongTermMemoryStore
from learned_policy_cortex import LearnedPolicyCortex
from memory_system import CompactMemoryStore
from moltbook_claim_assistant import extract_moltbook_claims, stored_claim, verification_text
from moltybook_client import MoltyBookClient, process_social_side_effects
from onboarding_clients import ClawRoyaleClient, build_claw_siwe_message
from onboarding_clients import OnboardingAPIError, _unwrap
from decision_engine import active_fallback_action, make_plan
from progression_cortex import ProgressionCortex
from quest_rush_cortex import QuestRushCortex
from risk_engine import progression_value_at_risk
from settlement_memory import settlement_lessons
from social_cortex import SocialCortex
from threat_engine import escape_action
from utility_cortex import UtilityCortex
from turn_state_model import TurnState
from wallet_identity import wallet_for_purpose
from x_oauth import authorization_url, parse_callback_url, pkce_pair


class HardeningTests(unittest.TestCase):
    def _isolated(self, tmp: str) -> IsolatedCerberusInstance:
        return IsolatedCerberusInstance.create(Path(tmp) / "isolated")

    def _require_eth_account(self):
        try:
            from eth_account import Account  # type: ignore
        except ModuleNotFoundError:
            self.skipTest("eth_account is not installed in this environment")
        return Account

    def test_repo_root_does_not_shadow_runtime_modules_with_stale_pseudocode(self) -> None:
        for filename in ("decision_engine.py", "turn_state_model.py", "main_loop,py"):
            self.assertFalse((ROOT / filename).exists(), filename)

    def test_malformed_snapshot_does_not_crash_parser(self) -> None:
        state = TurnState.from_snapshot(
            {
                "turn": "not-a-number",
                "view": {
                    "self": {"id": "me", "hp": "bad", "ep": None, "isAlive": "false"},
                    "currentRegion": {"id": 5, "isDeathZone": "false"},
                    "cooldownRemainingMs": "oops",
                    "alertGauge": "10",
                },
            }
        )

        self.assertEqual(state.turn, 0)
        self.assertEqual(state.self.hp, 0)
        self.assertFalse(state.self.is_alive)
        self.assertFalse(state.current_region.is_death_zone)
        self.assertTrue(state.alert_active)

    def test_parser_preserves_visible_monster_kind_when_type_is_species(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 5},
                    "currentRegion": {"id": "r1"},
                    "visibleMonsters": [{"id": "wolf-1", "type": "Wolf", "hp": 20}],
                }
            }
        )

        self.assertEqual(state.visible_monsters[0].kind, "monster")

    def test_parser_moves_guardians_from_visible_agents_to_monsters(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 5},
                    "currentRegion": {"id": "r1"},
                    "visibleAgents": [
                        {"id": "guardian-1", "name": "Guardian", "hp": 150, "atk": 10},
                        {"id": "rival-1", "name": "Rival", "hp": 40, "atk": 12},
                    ],
                }
            }
        )

        self.assertEqual([agent.id for agent in state.visible_agents], ["rival-1"])
        self.assertEqual([monster.id for monster in state.visible_monsters], ["guardian-1"])

    def test_parser_accepts_live_snapshot_aliases(self) -> None:
        state = TurnState.from_snapshot(
            {
                "gameId": "g1",
                "view": {
                    "agent": {"id": "me", "hp": 88, "ep": 4},
                    "region": {"id": "r7", "name": "Chapel"},
                    "monsters": [{"id": "guardian-1", "type": "Guardian", "hp": 80}],
                    "items": [{"id": "dagger-1", "typeId": "dagger"}],
                },
            }
        )

        self.assertEqual(state.self.id, "me")
        self.assertEqual(state.current_region.id, "r7")
        self.assertEqual(state.visible_monsters[0].kind, "monster")
        self.assertEqual(state.visible_items[0]["id"], "dagger-1")

    def test_parser_unwraps_visible_items_and_filters_remote_pickups(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 5},
                    "currentRegion": {"id": "r1"},
                    "visibleItems": [
                        {"regionId": "r1", "item": {"id": "local", "typeId": "katana"}},
                        {"regionId": "r2", "item": {"id": "remote", "typeId": "sniper"}},
                    ],
                }
            }
        )

        self.assertEqual([item["id"] for item in state.visible_items], ["local", "remote"])
        self.assertEqual([item["id"] for item in state.local_ground_items()], ["local"])

    def test_quest_rush_prioritizes_safe_ruin_exploration(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                    "currentRegion": {"id": "ruin-1", "name": "Old Vault", "terrain": "Ruin"},
                    "aliveCount": 40,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertTrue(any(result.intent == "quest_discover_ruin" for result in results))
        self.assertTrue(any(result.action and result.action["type"] == "explore" for result in results))

    def test_quest_rush_explores_with_visible_non_attackable_agent(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                    "currentRegion": {"id": "ruin-1", "terrain": "Ruin"},
                    "visibleAgents": [
                        {"id": "distant", "hp": 100, "isAlive": True, "attackable": False}
                    ],
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertTrue(any(result.action and result.action["type"] == "explore" for result in results))

    def test_quest_rush_blocks_ruin_exploration_during_attack_range_combat(self) -> None:
        for field in ("visibleAgents", "visibleMonsters"):
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                        "currentRegion": {"id": "ruin-1", "terrain": "Ruin"},
                        field: [{"id": "threat", "hp": 100, "isAlive": True, "attackable": True}],
                    }
                }
            )

            results = QuestRushCortex().evaluate(state, {})

            self.assertFalse(any(result.action and result.action["type"] == "explore" for result in results))

    def test_quest_rush_acquires_known_relic_with_capacity_and_reserves(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me", "hp": 100, "maxHp": 100, "ep": 3,
                        "inventory": [{"id": f"relic-{index}", "typeId": "relic_red"} for index in range(4)],
                    },
                    "currentRegion": {"id": "r1", "terrain": "Ruin"},
                },
                "events": [
                    {"type": "ruin_state_changed", "data": {"ruinId": "ru-1", "contentType": "relic", "gauge": 1}}
                ],
            }
        )

        result = next(
            result for result in QuestRushCortex().evaluate(state, {})
            if result.intent == "quest_relic_acquisition"
        )

        self.assertEqual(result.action["type"], "explore")

    def test_quest_rush_stops_known_relic_ruin_when_match_bag_is_full(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me", "hp": 100, "maxHp": 100, "ep": 5,
                        "inventory": [{"id": f"relic-{index}", "typeId": "relic_red"} for index in range(5)],
                    },
                    "currentRegion": {"id": "r1", "terrain": "Ruin"},
                },
                "events": [
                    {"type": "ruin_state_changed", "data": {"ruinId": "ru-1", "contentType": "relic", "gauge": 1}}
                ],
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.action and result.action["type"] == "explore" for result in results))

    def test_economy_skips_relic_pickup_when_match_bag_is_full(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me", "hp": 100, "maxHp": 100, "ep": 5,
                        "inventory": [{"id": f"owned-{index}", "typeId": "relic_red"} for index in range(5)],
                    },
                    "currentRegion": {
                        "id": "r1", "items": [{"id": "relic-extra", "typeId": "relic_blue"}]
                    },
                }
            }
        )

        results = EconomyCortex().evaluate(state, {})

        self.assertFalse(any(result.action and result.action.get("itemId") == "relic-extra" for result in results))

    def test_quest_rush_targets_safe_guardian_progress(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5, "atk": 40},
                    "currentRegion": {"id": "r1"},
                    "visibleMonsters": [
                        {"id": "guardian-1", "name": "Guardian", "hp": 25, "atk": 8, "regionId": "r1"}
                    ],
                    "aliveCount": 35,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})
        guardian = next(result for result in results if result.intent == "quest_guardian_kill")

        self.assertEqual(guardian.action["type"], "attack")
        self.assertEqual(guardian.action["targetId"], "guardian-1")
        self.assertNotIn("Q|season.kills", guardian.source_facts)

    def test_quest_rush_targets_only_favorable_rival_fights(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5, "atk": 45, "def": 10},
                    "currentRegion": {"id": "r1"},
                    "visibleAgents": [
                        {"id": "rival-1", "hp": 25, "maxHp": 100, "atk": 8, "def": 2, "regionId": "r1"}
                    ],
                    "aliveCount": 35,
                }
            }
        )

        result = next(
            result for result in QuestRushCortex().evaluate(state, {})
            if result.intent == "quest_safe_rival_hunt"
        )

        self.assertEqual(result.action["targetId"], "rival-1")
        self.assertIn("Q|season.rival_kills", result.source_facts)

    def test_quest_rush_rejects_dangerous_rival_fight(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 75, "maxHp": 100, "ep": 5, "atk": 15, "def": 4},
                    "currentRegion": {"id": "r1"},
                    "visibleAgents": [
                        {"id": "rival-1", "hp": 100, "maxHp": 100, "atk": 40, "def": 15, "regionId": "r1"}
                    ],
                    "aliveCount": 35,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "quest_safe_rival_hunt" for result in results))

    def test_quest_rush_banks_survival_near_top_ten_even_with_full_ep(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                    "currentRegion": {"id": "r1"},
                    "aliveCount": 10,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})
        reserve = next(result for result in results if result.intent == "quest_top10_reserve")

        self.assertEqual(reserve.action["type"], "rest")
        self.assertGreater(reserve.priority, 76)

    def test_quest_rush_does_not_rest_early_at_alive_twelve(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 2},
                    "currentRegion": {"id": "r1"},
                    "aliveCount": 12,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "quest_top10_reserve" for result in results))

    def test_quest_rush_stops_completed_or_enemy_occupied_ruin(self) -> None:
        for ruin_event in (
            {"ruinId": "ru-1", "gauge": 3, "maxGauge": 3},
            {"ruinId": "ru-1", "gauge": 1, "maxGauge": 3, "occupiedBy": "enemy"},
            {"ruinId": "ru-1", "gauge": 0, "maxGauge": 3, "isEmpty": True},
        ):
            with self.subTest(ruin_event=ruin_event):
                state = TurnState.from_snapshot(
                    {
                        "view": {
                            "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                            "currentRegion": {"id": "r1", "terrain": "Ruin"},
                        },
                        "events": [{"type": "ruin_state_changed", "data": ruin_event}],
                    }
                )

                results = QuestRushCortex().evaluate(state, {})

                self.assertFalse(any(result.intent == "quest_discover_ruin" for result in results))

    def test_quest_rush_stops_ruin_before_alert_threshold(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5},
                    "currentRegion": {"id": "r1", "terrain": "Ruin"},
                    "alertGauge": 7,
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "quest_discover_ruin" for result in results))

    def test_quest_rush_rejects_long_or_dangerous_guardian_fight(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me", "hp": 80, "maxHp": 100, "ep": 5,
                        "atk": 10, "def": 5, "equippedWeapon": {"typeId": "fist"},
                    },
                    "currentRegion": {"id": "r1"},
                    "visibleMonsters": [
                        {"id": "guardian-1", "name": "Guardian", "hp": 100, "atk": 25, "def": 10}
                    ],
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "quest_guardian_kill" for result in results))

    def test_quest_rush_keeps_one_ep_after_guardian_engagement(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 1, "atk": 100},
                    "currentRegion": {"id": "r1"},
                    "visibleMonsters": [
                        {"id": "guardian-1", "name": "Guardian", "hp": 10, "atk": 5, "regionId": "r1"}
                    ],
                }
            }
        )

        results = QuestRushCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "quest_guardian_kill" for result in results))

    def test_quest_rush_collects_generic_local_item_with_slot_reserve(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": False,
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5, "inventory": []},
                    "currentRegion": {"id": "r1"},
                    "visibleItems": [
                        {"regionId": "r1", "item": {"id": "map-1", "typeId": "map"}},
                        {"regionId": "r2", "item": {"id": "food-1", "typeId": "food"}},
                    ],
                },
            }
        )

        results = QuestRushCortex().evaluate(state, {})
        pickup = next(result for result in results if result.intent == "quest_item_pickup")

        self.assertEqual(pickup.action["itemId"], "map-1")

    def test_quest_rush_takes_free_item_before_exploration_rotation(self) -> None:
        plan = make_plan(
            state={
                "view": {
                    "self": {"id": "me", "hp": 100, "maxHp": 100, "ep": 5, "inventory": []},
                    "currentRegion": {
                        "id": "r1",
                        "connections": [{"id": "r2", "terrain": "Plains"}],
                        "items": [{"id": "map-1", "typeId": "map"}],
                    },
                }
            },
            cortexes=[QuestRushCortex()],
        )

        self.assertEqual(plan["action"]["type"], "pickup")
        self.assertEqual(plan["action"]["itemId"], "map-1")

    def test_tick_survives_non_dict_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()

            action = cerberus_tick("broken", memory_store=memory, dossier_store=dossiers)

        self.assertEqual(action["type"], "rest")

    def test_tick_rests_on_hollow_live_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()

            action = cerberus_tick({"gameId": "g1", "view": {}}, memory_store=memory, dossier_store=dossiers)

        self.assertEqual(action["type"], "rest")
        self.assertIn("usable live turn facts", action["reason"])


    def test_corrupt_memory_file_loads_empty_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "memory.json"
            path.write_text("{not-json", encoding="utf-8")

            store = CompactMemoryStore(
                path=path,
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()

        self.assertEqual(store.data["turns"], [])
        self.assertIn("load_warning", store.data)

    def test_corrupt_knowledge_file_loads_empty_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "knowledge.cak"
            path.write_bytes(b"not-gzip")

            kb = KnowledgeBase(path).load()

        self.assertEqual(kb.facts, [])
        self.assertIn("load_warning", kb.data)

    def test_dossier_loader_ignores_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dossiers.json"
            path.write_text(
                json.dumps({"records": {"agent-1": {"name": "A", "unknown": "x"}}}),
                encoding="utf-8",
            )

            store = AgentDossierStore(
                path=path,
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()

        self.assertEqual(store.records["agent-1"].agent_id, "agent-1")
        self.assertEqual(store.records["agent-1"].name, "A")

    def test_moltybook_disabled_is_non_networking(self) -> None:
        result = MoltyBookClient(enabled=False).post_draft({"content": "hello"})

        self.assertTrue(result["skipped"])
        self.assertIn("disabled", result["reason"])

    def test_invalid_action_is_demoted_to_rest(self) -> None:
        action = normalize_action({"type": "move"})

        self.assertEqual(action["type"], "rest")
        self.assertIn("missing regionId", action["reason"])

    def test_documented_free_chat_actions_are_validated(self) -> None:
        self.assertEqual(normalize_action({"type": "talk", "message": "hello"})["type"], "talk")
        self.assertEqual(
            normalize_action({"type": "whisper", "targetId": "agent-1", "message": "quiet"})["type"],
            "whisper",
        )
        self.assertEqual(normalize_action({"type": "interact", "targetId": "station-1"})["type"], "interact")
        self.assertEqual(normalize_action({"type": "whisper", "message": "missing target"})["type"], "rest")

    def test_legalizer_blocks_actions_that_exceed_contract_ep_cost(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 1},
                    "currentRegion": {"id": "storm-1", "terrain": "Storm"},
                },
            }
        )

        action = legalize_action({"type": "move", "regionId": "safe-1", "reason": "escape storm"}, state)

        self.assertNotEqual(action["type"], "move")
        self.assertIn("insufficient EP", action["reason"])
        self.assertEqual(action["_contract_cost"]["cost"], 2)
        self.assertEqual(action["_contract_cost"]["available_ep"], 1)

    def test_legalizer_prefers_live_available_action_cost(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 2},
                    "currentRegion": {"id": "r1", "terrain": "Plain"},
                    "availableActions": {"attack": {"cost": 3}},
                    "visibleAgents": [{"id": "enemy-1", "hp": 20, "isAlive": True}],
                },
            }
        )

        blocked = legalize_action({"type": "attack", "targetId": "enemy-1"}, state)

        self.assertNotEqual(blocked["type"], "attack")
        self.assertEqual(blocked["_contract_cost"]["cost"], 3)

    def test_autonomy_insufficient_ep_suggestion_includes_contract_cost(self) -> None:
        suggestions = autonomy_suggestions.suggested_edits_from_observation(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 1},
                    "currentRegion": {"id": "storm-1", "terrain": "Storm"},
                },
            },
            {"type": "move", "regionId": "safe-1"},
            outcome={"code": "INSUFFICIENT_EP", "message": "not enough EP"},
        )

        suggestion = next(item for item in suggestions if item["detector"] == "contract.insufficient_ep")
        self.assertIn("Action Cost: 2", suggestion["title"])

    def test_claw_v1_9_contract_captures_patch_economy(self) -> None:
        self.assertEqual(claw_contract.SHOP_ITEMS["random_pack_ticket"]["price_smoltz"], 25000)
        self.assertEqual(claw_contract.SHOP_ITEMS["reforge_stone_bundle"]["price_smoltz"], 3000)
        self.assertEqual(claw_contract.SHOP_ITEMS["random_profile_ticket"]["price_smoltz"], 50000)
        self.assertEqual(set(claw_contract.PACK_CATEGORY_DROP_RATE.values()), {0.20})
        self.assertEqual(claw_contract.PACK_TIER_WEIGHTS, {"T1": 1, "T2": 2, "T3": 3})
        self.assertEqual(claw_contract.REFORGE_STONE_WEIGHTS["stat_reroll"]["weight"], 1)
        self.assertEqual(claw_contract.TOP_UP["minimum_moltz"], 1000)

    def test_claw_v1_9_contract_captures_actions_and_affixes(self) -> None:
        self.assertTrue(claw_contract.is_free_action("pickup"))
        self.assertTrue(claw_contract.is_cooldown_action("attack"))
        self.assertEqual(claw_contract.action_cost("move"), 1)
        self.assertEqual(claw_contract.action_cost("move", terrain="storm"), 2)
        self.assertEqual(claw_contract.action_cost("attack", pack_category="goliath"), 2)
        self.assertEqual(claw_contract.AFFIX_POOL["sharp"], {"stat": "ITEM ATK", "direction": "+", "min": 5, "max": 15})
        self.assertEqual(len(claw_contract.AFFIX_POOL), 12)

    def test_claw_v1_9_contract_captures_readiness_and_runtime_skeleton(self) -> None:
        self.assertEqual(claw_contract.REST_API_BASE, "https://cdn.clawroyale.ai/api")
        self.assertEqual(claw_contract.WEBSOCKET_HOST, "wss://cdn.clawroyale.ai")
        self.assertEqual(claw_contract.ACTIVE_GAME_LIMITS, {"free": 1, "paid": 1})
        self.assertIn("balance_smoltz>=500", claw_contract.READINESS_GATES["paid_offchain"])
        self.assertIn("claw_wallet_moltz>=500", claw_contract.READINESS_GATES["paid_onchain"])
        self.assertEqual(claw_contract.READINESS_GATES["free"], ("api_key",))
        self.assertFalse(claw_contract.IDENTITY_RULES["required_for_free"])
        self.assertTrue(claw_contract.WALLET_RULES["agent_eoa_must_differ_from_owner_eoa"])
        self.assertTrue(claw_contract.IDENTITY_RULES["agentId_is_not_game_agent_uuid"])
        self.assertEqual(claw_contract.JOIN_WAIT_CAPS["free_assigned_seconds"], 120)
        self.assertEqual(claw_contract.JOIN_WAIT_CAPS["paid_joined_after_tx_seconds"], 30)

    def test_claw_v1_9_contract_captures_events_errors_and_loadout_semantics(self) -> None:
        self.assertIn("game_settled", claw_contract.EVENT_FRAMES)
        self.assertIn("ruin_state_changed", claw_contract.EVENT_FRAMES)
        self.assertEqual(claw_contract.LOADOUT["type_index"], {0: "red", 1: "green", 2: "blue"})
        self.assertEqual(claw_contract.LOADOUT["effective_stats_without_full_set"], 0)
        self.assertTrue(claw_contract.LOADOUT["sub_pack_required"])
        self.assertIn("sub_pack", claw_contract.LOADOUT["full_set_components"])
        self.assertEqual(claw_contract.LOADOUT["discard_equipped_relic"], "fails_409_until_unequipped")
        self.assertIn("RATE_LIMITED", claw_contract.ERROR_CODES)
        self.assertIn("ACCOUNT_ALREADY_IN_GAME", claw_contract.ERROR_CODES)
        self.assertIn("HELLO_TIMEOUT", claw_contract.JOIN_CLOSE_CODES)

    def test_stream_dashboard_cortex_sanitizes_public_state(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(
            runtime={
                "state": "blocked",
                "mode": "offchain",
                "version": "1.9.0",
                "last_frame_type": "sign_required",
                "last_error": "private key leaked in fake text",
            },
            current_game_id="game-1",
            chat=[
                stream_dashboard_cortex.chat_message("owner", "api key should not show"),
                {"author": "<script>", "message": "private key should not show"},
            ],
        )

        self.assertEqual(state["host"], "Hellion")
        self.assertEqual(state["spectate_url"], "https://www.clawroyale.ai/games/spect/game-1")
        self.assertIn("[private]", state["runtime"]["last_error"])
        self.assertIn("[private]", state["chat"][0]["message"])
        self.assertIn("[private]", state["chat"][1]["message"])
        self.assertNotIn("private key", json.dumps(state).lower())

    def test_stream_dashboard_cortex_sanitizes_spectate_game_id(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(runtime={"state": "playing"}, current_game_id='game-1" onload="alert(1)')

        self.assertEqual(state["current_game_id"], "game-1onloadalert1")
        self.assertEqual(state["spectate_url"], "https://www.clawroyale.ai/games/spect/game-1onloadalert1")

    def test_stream_dashboard_cortex_reports_public_blockers_without_game_id(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(runtime={"state": "reconnecting", "last_error": "INVALID_SIGNATURE"})

        self.assertFalse(state["ok"])
        self.assertIn("Waiting for a Claw Royale game ID", state["blockers"])
        self.assertIn("INVALID_SIGNATURE", state["blockers"])

    def test_stream_dashboard_cortex_exposes_voice_lab_soundbites(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(
            runtime={"state": "playing"},
            current_game_id="game-1",
            voice_lab={
                "source": "hellion voice lab",
                "soundbites": [
                    {"text": "Bring the thunder, keep the private key out.", "mood": "hype", "audio_url": "https://cdn.example/voice.mp3"}
                ],
            },
        )

        self.assertTrue(state["voice_lab"]["enabled"])
        self.assertIn("[private]", state["voice_lab"]["soundbites"][0]["text"])
        self.assertEqual(state["voice_lab"]["soundbites"][0]["mood"], "hype")

    def test_stream_dashboard_cortex_exposes_public_runtime_thought(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(
            runtime={"state": "playing", "last_public_thought": "Hellion applies peer review. Results may be terminal."},
            current_game_id="game-1",
        )

        self.assertIn("Hellion", state["thought"])
        self.assertNotIn("private key", state["thought"].lower())

    def test_tick_returns_action_when_save_fails(self) -> None:
        class BadMemory(CompactMemoryStore):
            def save(self, *args, **kwargs):  # type: ignore[no-untyped-def]
                raise OSError("disk full")

        with tempfile.TemporaryDirectory() as tmp:
            memory = BadMemory(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()

            action = cerberus_tick(
                {"view": {"self": {"id": "me", "hp": 50, "ep": 1}, "currentRegion": {"id": "r1"}}},
                memory_store=memory,
                dossier_store=dossiers,
            )

        self.assertEqual(action["type"], "rest")
        self.assertEqual(action["_warnings"][0]["type"], "save_error")

    def test_memory_rewrite_compacts_and_dedupes_under_churn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
                max_turns=6,
                max_lessons=4,
                max_facts=4,
            ).load()
            for index in range(20):
                store.remember_fact(f"fact-{index % 3}")
                store.remember_lesson("stress", f"lesson-{index % 2}")
                store.remember_turn({"turn": index, "view": {"self": {"hp": 100, "ep": 5}}})
            store.rewrite()
            reloaded = CompactMemoryStore(path=store.path, encrypted_path=store.encrypted_path).load()

        self.assertLessEqual(len(reloaded.data["facts"]), 4)
        self.assertLessEqual(len(reloaded.data["lessons"]), 4)
        self.assertLessEqual(len(reloaded.data["turns"]), 6)
        self.assertEqual(len(reloaded.data["facts"]), len(set(reloaded.data["facts"])))

    def test_memory_lesson_dedupes_across_different_timestamps(self) -> None:
        old_utc_now = memory_system.utc_now
        try:
            with tempfile.TemporaryDirectory() as tmp:
                memory_system.utc_now = lambda: "2026-06-17T00:00:00+00:00"  # type: ignore[assignment]
                store = CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load()
                store.remember_lesson("combat", "failure: Rival eliminated us in Storm Hall", source="event:death", confidence="0.9")
                memory_system.utc_now = lambda: "2026-06-17T00:00:02+00:00"  # type: ignore[assignment]
                store.remember_lesson("combat", "failure: Rival eliminated us in Storm Hall", source="event:death", confidence="0.9")
                store.rewrite()
                reloaded = CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load()
        finally:
            memory_system.utc_now = old_utc_now  # type: ignore[assignment]

        lessons = reloaded.data["lessons"]
        self.assertEqual(len(lessons), 1)
        self.assertIn("Rival eliminated us", lessons[0])

    @unittest.skipUnless(os.name == "nt", "DPAPI vault roundtrip is Windows-specific")
    def test_memory_encrypt_decrypt_roundtrip_in_isolated_instance(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        os.environ["CERBERUS_PIN"] = "2468"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                isolated = self._isolated(tmp)
                isolated.tick({"turn": 1, "view": {"self": {"id": "me", "hp": 90, "ep": 5}, "currentRegion": {"id": "r1"}}})
                path = isolated.memory.rewrite()
                reloaded = isolated.reload()
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

        self.assertTrue(str(path).endswith(".vault.json"))
        self.assertEqual(len(reloaded.memory.data["turns"]), 1)

    @unittest.skipUnless(os.name == "nt", "DPAPI wrong-PIN behavior is Windows-specific")
    def test_memory_wrong_pin_loads_empty_with_warning(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_PIN"] = "2468"
                isolated = self._isolated(tmp)
                isolated.tick({"turn": 1, "view": {"self": {"id": "me", "hp": 90, "ep": 5}, "currentRegion": {"id": "r1"}}})
                isolated.memory.rewrite()

                os.environ["CERBERUS_PIN"] = "1357"
                reloaded = isolated.reload()
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

        self.assertEqual(reloaded.memory.data["turns"], [])
        self.assertIn("load_warning", reloaded.memory.data)

    def test_compact_memory_falls_back_to_plaintext_when_dpapi_unavailable(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        old_available = memory_system.compact_memory_encryption_available
        try:
            os.environ["CERBERUS_PIN"] = "2468"
            memory_system.compact_memory_encryption_available = lambda: False  # type: ignore[assignment]
            with tempfile.TemporaryDirectory() as tmp:
                store = CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load()
                store.remember_turn({"view": {"self": {"id": "me", "hp": 90, "ep": 5}}})
                path = store.save()
                reloaded = CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load()
        finally:
            memory_system.compact_memory_encryption_available = old_available  # type: ignore[assignment]
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

        self.assertTrue(str(path).endswith("memory.json"))
        self.assertEqual(reloaded.data["storage"]["mode"], "plaintext_compact_nonwindows")
        self.assertNotIn("warning", reloaded.data["integrity"])
        self.assertEqual(len(reloaded.data["turns"]), 1)

    def test_tick_writes_sanitized_longterm_memory_when_store_is_wired(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            longterm = LongTermMemoryStore(Path(tmp) / "hellion.longterm.sqlite")

            action = cerberus_tick(
                {
                    "gameId": "g1",
                    "turn": 7,
                    "view": {
                        "self": {"id": "me", "hp": 91, "maxHp": 100, "ep": 4, "maxEp": 10},
                        "currentRegion": {"id": "r1", "name": "Ruin Mouth", "terrain": "ruin"},
                        "visibleItems": [{"id": "dagger-1", "typeId": "dagger"}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                longterm_store=longterm,
            )

            rows = longterm.top(kind="turn", scope="claw_royale", limit=5)
            self.assertEqual(action["type"], "pickup")
            self.assertTrue(rows)
            self.assertEqual(len(rows), 1)
            self.assertIn("action=pickup", rows[0]["text"])
            self.assertNotIn("private", rows[0]["text"].lower())

    def test_mongo_memory_store_writes_compact_items(self) -> None:
        class FakeCursor(list):
            def sort(self, _fields):  # type: ignore[no-untyped-def]
                return self

            def limit(self, count):  # type: ignore[no-untyped-def]
                return FakeCursor(self[:count])

        class FakeCollection:
            name = "hellion_memory_items"

            def __init__(self):  # type: ignore[no-untyped-def]
                self.rows = {}
                self.indexes = []

            def create_index(self, fields, **kwargs):  # type: ignore[no-untyped-def]
                self.indexes.append((fields, kwargs))

            def update_one(self, query, update, upsert=False):  # type: ignore[no-untyped-def]
                row = dict(update["$setOnInsert"])
                row.update(update["$set"])
                row["hits"] = update["$inc"]["hits"]
                self.rows[query["source_hash"]] = row

            def find(self, query):  # type: ignore[no-untyped-def]
                rows = []
                for row in self.rows.values():
                    if all(row.get(key) == value for key, value in query.items()):
                        rows.append(row)
                return FakeCursor(rows)

            def count_documents(self, query):  # type: ignore[no-untyped-def]
                return len(list(self.find(query)))

        class FakeDatabase(dict):
            def __getitem__(self, name):  # type: ignore[no-untyped-def]
                if name not in self:
                    self[name] = FakeCollection()
                return dict.__getitem__(self, name)

        class FakeClient(dict):
            def __getitem__(self, name):  # type: ignore[no-untyped-def]
                if name not in self:
                    self[name] = FakeDatabase()
                return dict.__getitem__(self, name)

        store = mongo_memory.MongoLongTermMemoryStore(client=FakeClient(), database="cerberus")
        item_id = store.remember(
            kind="turn",
            scope="claw_royale",
            key="move:ruin",
            text="turn action=move; private_key=should be compacted before here",
            confidence=0.8,
            importance=55,
        )

        rows = store.top(kind="turn", scope="claw_royale", limit=1)
        self.assertEqual(rows[0]["id"], item_id)
        self.assertEqual(store.stats()["backend"], "mongo")

    def test_stats_does_not_treat_stale_stored_game_id_as_live(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.remember_game_id("stale-game")
                runtime_state.update_claw_runtime_status(state="connected", current_game_id="", game_status="")
                payload = render_app.stats()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(payload["current_game_id"], "")
        self.assertEqual(payload["spectate_url"], "")

    def test_stream_dashboard_does_not_call_joined_without_game_live(self) -> None:
        cortex = stream_dashboard_cortex.StreamDashboardCortex(
            spectate_base_url="https://www.clawroyale.ai/games/spect"
        )
        state = cortex.public_state(runtime={"state": "joined"}, current_game_id="")

        self.assertFalse(state["ok"])
        self.assertEqual(state["status"], "Paid-room entry in progress")
        self.assertFalse(any(alert["kind"] == "live" for alert in state["stream"]["alerts"]))

    def test_tick_learns_from_success_failure_and_other_agent_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            isolated.tick(
                {
                    "turn": 12,
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 72, "ep": 3},
                        "currentRegion": {"id": "r1", "name": "Arena Ring", "terrain": "Plain"},
                        "recentMessages": [{"agentId": "ally-1", "message": "ruin strategy: leave before alert spikes"}],
                    },
                    "events": [
                        {"type": "agent_kill", "data": {"killerId": "me", "victimId": "enemy-1", "victimName": "Rival"}},
                        {"type": "agent_kill", "data": {"killerId": "enemy-2", "killerName": "Hunter", "victimId": "me"}},
                        {"type": "agent_kill", "data": {"killerId": "enemy-3", "killerName": "Closer", "victimId": "enemy-4", "victimName": "Runner"}},
                        {"type": "relic_acquired", "data": {"agentId": "me", "contentType": "relic_red"}},
                    ],
                }
            )

            lessons = isolated.memory.data["lessons"]
            dossiers = isolated.dossiers.records

        self.assertTrue(any("success: eliminated Rival" in lesson for lesson in lessons))
        self.assertTrue(any("failure: Hunter eliminated us" in lesson for lesson in lessons))
        self.assertTrue(any("observed: Closer eliminated Runner" in lesson for lesson in lessons))
        self.assertTrue(any("secured relic_red" in lesson for lesson in lessons))
        self.assertEqual(dossiers["enemy-1"].killed_by_us, 1)
        self.assertEqual(dossiers["enemy-2"].killed_us, 1)
        self.assertIn("beat_Runner@Arena Ring", dossiers["enemy-3"].social_notes)

    def test_tick_registers_self_death_from_sparse_death_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            isolated.tick(
                {
                    "turn": 13,
                    "canAct": False,
                    "view": {
                        "self": {"id": "me", "name": "Hellion", "hp": 0, "ep": 0, "isAlive": False},
                        "currentRegion": {"id": "r1", "name": "Collapse Ring", "terrain": "Plain"},
                    },
                    "events": [
                        {"type": "agent_death", "data": {"killerId": "enemy-2", "killerName": "Hunter", "isAlive": False}},
                    ],
                }
            )

            lessons = isolated.memory.data["lessons"]
            dossiers = isolated.dossiers.records

        self.assertTrue(any("failure: Hunter eliminated us" in lesson for lesson in lessons))
        self.assertEqual(dossiers["enemy-2"].killed_us, 1)

    def test_tick_persists_validated_strategy_into_compact_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            isolated.tick(
                {
                    "turn": 8,
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 4},
                        "currentRegion": {"id": "r1", "name": "Ruin Gate", "terrain": "Ruin"},
                        "recentMessages": [{"agentId": "ally-1", "message": "Ruin strategy: leave before alert spikes and keep an exit open."}],
                    },
                }
            )

            lessons = isolated.memory.data["lessons"]
            dossier = isolated.dossiers.records["ally-1"]

        self.assertTrue(any("validated strat from ally-1" in lesson for lesson in lessons))
        self.assertTrue(any(note.startswith("validated:") for note in dossier.social_notes))

    def test_tick_learns_observed_loot_winner_as_future_intercept_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            isolated.tick(
                {
                    "view": {
                        "self": {"id": "me", "name": "Hellion", "hp": 100, "ep": 4, "isAlive": True},
                        "currentRegion": {"id": "r1", "name": "Arena Ring"},
                    },
                    "events": [
                        {
                            "type": "relic_acquired",
                            "data": {"agentId": "enemy-7", "agentName": "Carrier", "contentType": "relic_red"},
                        }
                    ],
                }
            )

            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 95,
                            "maxHp": 100,
                            "ep": 4,
                            "atk": 28,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Arena Ring"},
                        "visibleAgents": [{"id": "enemy-7", "name": "Carrier", "hp": 34, "atk": 8, "def": 2, "regionId": "r1"}],
                    },
                }
            )
            dossier = isolated.dossiers.records["enemy-7"]

        self.assertIn("collects_high_value_loot", dossier.observed_tendencies)
        self.assertIn("collects_loadout", dossier.observed_tendencies)
        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "enemy-7")
        self.assertIn("tends to leave fights with value", action["reason"])

    def test_cannot_act_blocks_main_actions_but_free_equip_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": False,
                    "cooldownRemainingMs": 15000,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 18,
                            "ep": 0,
                            "inventory": [
                                {"id": "med-1", "typeId": "medkit"},
                                {"id": "kat-1", "typeId": "katana"},
                            ],
                        },
                        "currentRegion": {"id": "r1", "name": "Ruin Edge"},
                        "visibleMonsters": [{"id": "m1", "hp": 10, "atk": 12}],
                    },
                }
            )

        self.assertEqual(action["type"], "equip")
        self.assertEqual(action["itemId"], "kat-1")

    def test_free_action_equips_highest_dps_weapon_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": False,
                    "cooldownRemainingMs": 9000,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 85,
                            "ep": 0,
                            "equippedWeapon": {"typeId": "dagger"},
                            "inventory": [
                                {"id": "blade-1", "typeId": "sword"},
                                {"id": "blade-2", "typeId": "sniper"},
                            ],
                        },
                        "currentRegion": {"id": "r1"},
                    },
                }
            )

        self.assertEqual(action["type"], "equip")
        self.assertEqual(action["itemId"], "blade-2")

    def test_low_hp_heals_when_main_action_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 14,
                            "ep": 3,
                            "inventory": [{"id": "med-1", "typeId": "medkit"}],
                        },
                        "currentRegion": {"id": "r1"},
                    },
                }
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")

    def test_mid_hp_heals_before_risky_combat(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 52,
                            "maxHp": 100,
                            "ep": 3,
                            "inventory": [{"id": "med-1", "typeId": "medkit"}],
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleMonsters": [{"id": "m1", "hp": 18, "atk": 12, "def": 4}],
                    },
                }
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")

    def test_death_zone_pressure_uses_visible_region_when_connections_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 3},
                        "currentRegion": {"id": "r1", "isDeathZone": True},
                        "visibleRegions": [
                            {"id": "r1", "isDeathZone": True},
                            {"id": "r2", "isDeathZone": False},
                        ],
                    },
                }
            )

        self.assertEqual(action["type"], "move")
        self.assertEqual(action["regionId"], "r2")

    def test_death_zone_pressure_explores_instead_of_resting_when_no_region_parse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 2},
                        "currentRegion": {"id": "r1", "isDeathZone": True},
                    },
                }
            )

        self.assertEqual(action["type"], "explore")

    def test_active_fallback_action_skips_pending_death_zone_region(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 80, "ep": 2},
                    "currentRegion": {"id": "r1", "name": "Field", "terrain": "Plain"},
                    "connectedRegions": [{"id": "r2", "name": "Marked", "terrain": "Plain"}],
                    "visibleRegions": [{"id": "r2", "name": "Marked", "terrain": "Plain"}],
                    "pendingDeathzones": [{"id": "r2"}],
                },
            }
        )

        action = active_fallback_action(state)

        self.assertEqual(action["type"], "explore")
        self.assertNotEqual(action.get("regionId"), "r2")

    def test_escape_action_skips_pending_death_zone_region(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 80, "ep": 2},
                    "currentRegion": {"id": "r1", "name": "Field", "terrain": "Plain", "isDeathZone": True},
                    "connectedRegions": [{"id": "r2", "name": "Marked", "terrain": "Plain"}],
                    "pendingDeathzones": [{"id": "r2"}],
                },
            }
        )

        action = escape_action(state)

        self.assertNotEqual(action.get("type"), "move")
        self.assertNotEqual(action.get("regionId"), "r2")

    def test_turn_state_parses_alternate_connection_fields(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "hp": 80, "ep": 3},
                    "currentRegion": {"id": "r1", "adjacentRegions": [{"id": "r2"}]},
                }
            }
        )

        self.assertEqual(state.connected_safe_regions()[0]["id"], "r2")

    def test_favorable_fight_attacks_when_main_action_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Arena Edge"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Rival", "hp": 12, "atk": 9, "def": 2}],
                    },
                }
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "enemy-1")

    def test_combat_does_not_attack_out_of_range_visible_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "dagger"},
                        },
                        "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                        "visibleAgents": [
                            {"id": "enemy-1", "name": "Rival", "hp": 12, "atk": 9, "def": 2, "distance": 2}
                        ],
                    },
                }
            )

        self.assertNotEqual(action["type"], "attack")
        self.assertEqual(action["type"], "move")

    def test_ranged_weapon_allows_visible_target_within_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "sniper"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [
                            {"id": "enemy-1", "name": "Rival", "hp": 12, "atk": 9, "def": 2, "distance": 2}
                        ],
                    },
                }
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "enemy-1")

    def test_legalizer_redirects_out_of_range_attack(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 3, "equippedWeapon": {"typeId": "fist"}},
                    "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                    "visibleAgents": [{"id": "enemy-1", "hp": 10, "distance": 1}],
                },
            }
        )

        action = legalize_action({"type": "attack", "targetId": "enemy-1"}, state)

        self.assertEqual(action["type"], "move")
        self.assertIn("_rejected_action", action)

    def test_weapon_on_ground_is_picked_up_before_bad_attack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 8,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "fist"},
                        },
                        "currentRegion": {
                            "id": "r1",
                            "items": [{"id": "dagger-1", "typeId": "dagger"}],
                        },
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 8, "def": 8}],
                    },
                }
            )

        self.assertEqual(action["type"], "pickup")
        self.assertEqual(action["itemId"], "dagger-1")

    def test_duplicate_ground_weapon_is_not_picked_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "dagger"},
                        },
                        "currentRegion": {
                            "id": "r1",
                            "items": [{"id": "dagger-2", "typeId": "dagger"}],
                        },
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 20, "atk": 8, "def": 2}],
                    },
                }
            )

        self.assertNotEqual(action.get("itemId"), "dagger-2")

    def test_energy_item_is_used_before_rest_when_ep_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 90,
                            "ep": 0,
                            "inventory": [{"id": "energy-1", "typeId": "energy_drink"}],
                        },
                        "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                    },
                }
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "energy-1")

    def test_removed_map_item_is_not_used_when_navigation_is_sparse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 90,
                            "ep": 4,
                            "inventory": [{"id": "map-1", "typeId": "map"}],
                        },
                        "currentRegion": {"id": "r1"},
                    },
                }
            )

        self.assertFalse(action.get("type") == "use_item" and action.get("itemId") == "map-1")

    def test_inventory_weapon_equips_before_attacking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 96,
                            "ep": 4,
                            "atk": 8,
                            "inventory": [{"id": "dagger-1", "typeId": "dagger"}],
                            "equippedWeapon": {"typeId": "fist"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 8, "def": 8}],
                    },
                }
            )

        self.assertEqual(action["type"], "equip")
        self.assertEqual(action["itemId"], "dagger-1")

    def test_moltz_pickup_is_preferred_when_area_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3, "inventory": []},
                        "currentRegion": {"id": "r1", "items": [{"id": "cash-1", "typeId": "smoltz_bundle"}]},
                    },
                }
            )

        self.assertEqual(action["type"], "pickup")
        self.assertEqual(action["itemId"], "cash-1")

    def test_moltz_pickup_is_preferred_before_contested_attack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "items": [{"id": "cash-1", "typeId": "smoltz_bundle"}]},
                        "visibleAgents": [{"id": "enemy-1", "hp": 12, "atk": 8, "def": 2}],
                    },
                }
            )

        self.assertEqual(action["type"], "pickup")
        self.assertEqual(action["itemId"], "cash-1")

    def test_relic_pickup_is_preferred_before_loose_smoltz(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3, "inventory": []},
                        "currentRegion": {
                            "id": "r1",
                            "items": [
                                {"id": "cash-1", "typeId": "smoltz_bundle"},
                                {"id": "relic-1", "typeId": "relic_red"},
                            ],
                        },
                    },
                }
            )

        self.assertEqual(action["type"], "pickup")
        self.assertEqual(action["itemId"], "relic-1")

    def test_owner_value_command_prioritizes_relic_before_smoltz(self) -> None:
        plan = make_plan(
            state={
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 100, "ep": 3, "inventory": []},
                    "currentRegion": {
                        "id": "r1",
                        "items": [
                            {"id": "cash-1", "typeId": "smoltz_bundle"},
                            {"id": "relic-1", "typeId": "relic_blue"},
                        ],
                    },
                },
            },
            cortexes=[owner_command_cortex.OwnerCommandCortex(), EconomyCortex()],
            owner_messages=[{"kind": "owner_command", "text": "prioritize relics and build the loadout"}],
        )

        self.assertEqual(plan["action"]["type"], "pickup")
        self.assertEqual(plan["action"]["itemId"], "relic-1")
        self.assertEqual(plan["winner"]["intent"], "owner_requested_value_pickup")

    def test_economy_uses_passed_dossiers_for_moltz_carrier_opportunity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.observe_agent("carrier-1", name="Carrier", tendency="collects_smoltz")
            plan = make_plan(
                state={
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 28,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [{"id": "carrier-1", "name": "Carrier", "hp": 34, "atk": 6, "def": 2, "regionId": "r1"}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                cortexes=[EconomyCortex()],
            )

        self.assertEqual(plan["action"]["type"], "attack")
        self.assertEqual(plan["action"]["targetId"], "carrier-1")
        self.assertEqual(plan["winner"]["intent"], "hunt_moltz_carrier")

    def test_economy_does_not_hunt_moltz_carrier_without_ep(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.observe_agent("carrier-1", name="Carrier", tendency="collects_smoltz")
            plan = make_plan(
                state={
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 0,
                            "atk": 28,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [{"id": "carrier-1", "name": "Carrier", "hp": 34, "atk": 6, "def": 2, "regionId": "r1"}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                cortexes=[EconomyCortex()],
            )

        self.assertEqual(plan["action"]["type"], "rest")
        self.assertIn("EP empty", plan["action"]["reason"])

    def test_combat_skips_low_damage_guardian_chipping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 4,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "fist"},
                        },
                        "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 8, "def": 8}],
                    },
                }
            )

        self.assertNotEqual(action["type"], "attack")
        self.assertEqual(action["type"], "move")

    def test_combat_prefers_killable_guardian_over_nonlethal_rival(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 20,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 70, "atk": 8, "def": 3}],
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 24, "atk": 8, "def": 4}],
                    },
                }
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "guardian-1")

    def test_owner_command_can_request_heal_before_low_hp_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 72,
                            "maxHp": 100,
                            "ep": 4,
                            "inventory": [{"id": "med-1", "typeId": "medkit"}],
                        },
                        "currentRegion": {"id": "r1"},
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                owner_command_messages=[{"kind": "owner_command", "text": "heal now before you get cute"}],
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")
        self.assertIn("owner directive", action["reason"])

    def test_owner_command_can_avoid_fighting_when_safe_move_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                        "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 12, "atk": 8, "def": 2}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                owner_command_messages=[{"kind": "owner_command", "text": "avoid fights and reposition"}],
            )

        self.assertEqual(action["type"], "move")
        self.assertEqual(action["regionId"], "r2")
        self.assertIn("owner directive", action["reason"])

    def test_owner_command_context_is_passed_to_custom_planner(self) -> None:
        seen = {}

        def planner(**kwargs):  # type: ignore[no-untyped-def]
            seen["owner_messages"] = kwargs.get("owner_messages")
            return {"action": {"type": "rest", "reason": "planner stub"}}

        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            cerberus_tick(
                {"view": {"self": {"id": "me", "hp": 100, "ep": 3}, "currentRegion": {"id": "r1"}}},
                memory_store=memory,
                dossier_store=dossiers,
                make_plan=planner,
                owner_command_messages=[{"kind": "owner_command", "text": "prioritize value"}],
            )

        self.assertEqual(seen["owner_messages"][0]["text"], "prioritize value")

    def test_owner_command_acknowledges_agreement_and_ignores_hellion_replies(self) -> None:
        command = {"kind": "owner_command", "text": "heal and prioritize value"}
        ack = owner_command_cortex.acknowledge_owner_command(command)
        directive = owner_command_cortex.latest_directive(
            [
                command,
                {"kind": "hellion_response", "author": "Hellion", "text": "I heard you."},
            ]
        )

        self.assertEqual(ack["status"], "agreed")
        self.assertIn("high-priority owner intent", ack["text"])
        self.assertEqual(directive, command)

    def test_owner_command_tick_writes_hellion_execution_response(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                memory = CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load()
                dossiers = AgentDossierStore(
                    path=Path(tmp) / "dossiers.json",
                    encrypted_path=Path(tmp) / "dossiers.vault.json",
                ).load()
                action = cerberus_tick(
                    {
                        "canAct": True,
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": 72,
                                "maxHp": 100,
                                "ep": 4,
                                "inventory": [{"id": "med-1", "typeId": "medkit"}],
                            },
                            "currentRegion": {"id": "r1"},
                        },
                    },
                    memory_store=memory,
                    dossier_store=dossiers,
                    owner_command_messages=[
                        {"id": "cmd-1", "kind": "owner_command", "text": "heal now"}
                    ],
                )
                responses = runtime_state.owner_messages()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(responses[-1]["kind"], "hellion_response")
        self.assertEqual(responses[-1]["command_id"], "cmd-1")
        self.assertEqual(responses[-1]["status"], "executing")

    def test_owner_command_can_request_guardian_attack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 20,
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 5, "atk": 5, "def": 1}],
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 22, "atk": 8, "def": 4}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                owner_command_messages=[{"kind": "owner_command", "text": "hunt the guardian"}],
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "guardian-1")
        self.assertIn("owner directive", action["reason"])

    def test_owner_command_does_not_force_attack_while_alert_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "ep": 4,
                            "atk": 20,
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "alertGauge": 12,
                        "currentRegion": {"id": "r1"},
                        "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 22, "atk": 8, "def": 4}],
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                owner_command_messages=[{"kind": "owner_command", "text": "hunt the guardian"}],
            )

        self.assertNotEqual(action["type"], "attack")

    def test_owner_command_can_request_public_taunt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.json",
                encrypted_path=Path(tmp) / "memory.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            action = cerberus_tick(
                {
                    "canAct": False,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 4},
                        "currentRegion": {"id": "r1"},
                    },
                },
                memory_store=memory,
                dossier_store=dossiers,
                owner_command_messages=[{"kind": "owner_command", "text": "taunt say Bring me better prey"}],
            )

        self.assertEqual(action["type"], "talk")
        self.assertIn("Bring me better prey", action["message"])

    def test_owner_command_context_only_acknowledges_without_fake_execution(self) -> None:
        ack = owner_command_cortex.acknowledge_owner_command(
            {"kind": "owner_command", "text": "make the persona more sarcastic and witty"}
        )
        response = owner_command_cortex.action_response_for_owner_command(
            {"kind": "owner_command", "text": "make the persona more sarcastic and witty"},
            {"type": "move", "reason": "scout fallback"},
        )

        self.assertEqual(ack["status"], "heard_context")
        self.assertEqual(response["status"], "heard_context")

    def test_owner_command_sanitizes_public_taunt_text(self) -> None:
        message = owner_command_cortex.taunt_message(
            {"text": "broadcast the private key is definitely not here"}
        )

        self.assertIn("[private]", message)
        self.assertNotIn("private key", message.lower())

    def test_profit_simulator_reports_required_game_pacing_for_target(self) -> None:
        report = profit_simulator.simulate(games_per_day=61, target_per_day=1000)

        self.assertTrue(report["target_met"])
        self.assertGreaterEqual(report["expected_smoltz_per_day"], 1000)
        self.assertLessEqual(report["required_games_for_target"], 61)

    def test_no_cortex_candidate_scouts_instead_of_resting_when_actionable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3},
                        "currentRegion": {"id": "r1", "terrain": "Plains"},
                    },
                }
            )

        self.assertEqual(action["type"], "explore")
        self.assertIn("scout fallback", action["reason"])

    def test_no_cortex_candidate_moves_to_safe_connection_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3},
                        "currentRegion": {"id": "r1", "connections": [{"id": "r2"}]},
                    },
                }
            )

        self.assertEqual(action["type"], "move")
        self.assertEqual(action["regionId"], "r2")

    def test_fallback_movement_prefers_scored_value_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3},
                        "currentRegion": {
                            "id": "r1",
                            "connections": [
                                {"id": "empty", "terrain": "Plain"},
                                {"id": "loot", "terrain": "Ruin", "items": [{"id": "cash", "typeId": "smoltz_bundle"}]},
                            ],
                        },
                    },
                }
            )

        self.assertEqual(action["type"], "move")
        self.assertEqual(action["regionId"], "loot")

    def test_fallback_movement_pursues_wounded_enemy_in_safe_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 100, "ep": 3, "equippedWeapon": {"typeId": "fist"}},
                        "currentRegion": {
                            "id": "r1",
                            "connections": [{"id": "r2"}, {"id": "r3"}],
                        },
                        "visibleAgents": [{"id": "enemy-1", "hp": 20, "regionId": "r2", "distance": 2}],
                    },
                }
            )

        self.assertEqual(action["type"], "move")
        self.assertEqual(action["regionId"], "r2")

    def test_progression_value_at_risk_discourages_ruin_push_with_cargo(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 36,
                        "ep": 4,
                        "inventory": [
                            {"id": "relic-1", "typeId": "relic_red"},
                            {"id": "pack-1", "typeId": "goliath_pack"},
                        ],
                    },
                    "alertGauge": 9,
                    "currentRegion": {"id": "r1", "terrain": "Ruin"},
                },
            }
        )
        risk = progression_value_at_risk(state)
        results = ProgressionCortex().evaluate(state, {})

        self.assertGreaterEqual(risk, 50)
        self.assertTrue(results)
        self.assertGreaterEqual(results[0].risk, risk)

    def test_progression_cargo_protection_heals_before_relic_greed(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 34,
                        "maxHp": 100,
                        "ep": 4,
                        "inventory": [
                            {"id": "relic-1", "typeId": "relic_red"},
                            {"id": "med-1", "typeId": "medkit"},
                        ],
                    },
                    "currentRegion": {"id": "r1", "terrain": "Ruin"},
                },
            }
        )
        results = ProgressionCortex().evaluate(state, {})
        heal = next(result for result in results if result.intent == "preserve_cargo_heal")

        self.assertTrue(heal.veto)
        self.assertEqual(heal.action["type"], "use_item")
        self.assertEqual(heal.action["itemId"], "med-1")

    def test_progression_cargo_protection_repositions_out_of_deathzone(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 70,
                        "ep": 3,
                        "inventory": [{"id": "pack-1", "typeId": "scout_pack"}],
                    },
                    "currentRegion": {"id": "r1", "terrain": "Plain", "isDeathZone": True},
                    "connectedRegions": [
                        {"id": "safe-1", "name": "Medical Annex", "terrain": "Plain"},
                        {"id": "ruin-1", "name": "Old Ruin", "terrain": "Ruin"},
                    ],
                },
            }
        )
        results = ProgressionCortex().evaluate(state, {})
        move = next(result for result in results if result.intent == "preserve_cargo_reposition")

        self.assertTrue(move.veto)
        self.assertEqual(move.action["type"], "move")
        self.assertEqual(move.action["regionId"], "safe-1")

    def test_progression_cargo_protection_repositions_out_of_pending_deathzone(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 70,
                        "ep": 3,
                        "inventory": [{"id": "pack-1", "typeId": "scout_pack"}],
                    },
                    "currentRegion": {"id": "r1", "terrain": "Plain", "isDeathZone": False},
                    "pendingDeathzones": [{"id": "r1"}],
                    "connectedRegions": [
                        {"id": "safe-1", "name": "Medical Annex", "terrain": "Plain"},
                        {"id": "ruin-1", "name": "Old Ruin", "terrain": "Ruin"},
                    ],
                },
            }
        )
        results = ProgressionCortex().evaluate(state, {})
        move = next(result for result in results if result.intent == "preserve_cargo_reposition")

        self.assertTrue(move.veto)
        self.assertEqual(move.action["type"], "move")
        self.assertEqual(move.action["regionId"], "safe-1")

    def test_progression_action_escapes_pending_deathzone_before_exploring_ruins(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 84, "ep": 3},
                    "currentRegion": {"id": "r1", "terrain": "Ruin", "isDeathZone": False},
                    "pendingDeathzones": [{"id": "r1"}],
                    "connectedRegions": [{"id": "safe-1", "name": "Medical Annex", "terrain": "Plain"}],
                },
            }
        )

        self.assertEqual(
            owner_command_cortex.progression_action(state),
            {"type": "move", "regionId": "safe-1", "reason": "owner directive: escape pending death zone before progression"},
        )

    def test_utility_map_use_is_blocked_in_pending_deathzone(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 84, "ep": 3, "inventory": [{"id": "map-1", "typeId": "map"}]},
                    "currentRegion": {"id": "r1", "terrain": "Plain", "isDeathZone": False},
                    "pendingDeathzones": [{"id": "r1"}],
                    "visibleRegions": [{"id": "r2", "name": "Far Field", "terrain": "Plain"}],
                    "connectedRegions": [{"id": "r2", "name": "Far Field", "terrain": "Plain"}],
                },
            }
        )
        results = UtilityCortex().evaluate(state, {})

        self.assertFalse(any(result.intent == "use_map_for_navigation" for result in results))

    def test_free_action_equips_live_armor_upgrade(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 80,
                        "ep": 4,
                        "equippedArmor": {"id": "cloth", "defBonus": 2},
                        "inventory": [{"id": "plate", "name": "Plate", "defBonus": 8}],
                    },
                    "currentRegion": {"id": "r1"},
                }
            }
        )

        armor = next(result for result in FreeActionCortex().evaluate(state, {}) if result.intent == "equip_best_armor")

        self.assertEqual(armor.action, {"type": "equip", "itemId": "plate"})

    def test_explicit_recovery_fields_drive_item_choice(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {
                        "id": "me",
                        "hp": 40,
                        "ep": 0,
                        "inventory": [
                            {"id": "unknown-heal", "hpRestore": 35},
                            {"id": "unknown-energy", "epRestore": 6},
                        ],
                    }
                }
            }
        )

        self.assertEqual(state.best_heal_item()["id"], "unknown-heal")
        self.assertEqual(state.best_energy_item()["id"], "unknown-energy")

    def test_megaphone_no_longer_unlocks_broadcast_without_station(self) -> None:
        state = TurnState.from_snapshot(
            {
                "view": {
                    "self": {"id": "me", "inventory": [{"id": "meg", "typeId": "megaphone"}]},
                    "currentRegion": {"id": "r1", "interactables": []},
                }
            }
        )

        self.assertFalse(state.has_broadcast_channel)

    def test_settlement_memory_extracts_compact_lessons(self) -> None:
        lessons = settlement_lessons(
            {
                "events": [
                    {
                        "type": "game_settled",
                        "data": {
                            "settlement": {
                                "finalRank": 31,
                                "kills": 0,
                                "rewards": {"sMoltz": 0},
                            }
                        },
                    }
                ]
            }
        )

        self.assertTrue(any("zero kills" in lesson for lesson in lessons))
        self.assertTrue(any("rank 31" in lesson for lesson in lessons))

    def test_social_side_effect_failures_are_captured_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 4, "inventory": [{"id": "meg", "typeId": "megaphone"}]},
                        "currentRegion": {"id": "r1", "interactables": [{"type": "broadcast_station"}]},
                        "visibleAgents": [{"id": "enemy-1", "name": "Rival", "moltybookHandle": "@rival"}],
                    },
                    "events": [{"type": "agent_kill", "data": {"killerId": "me", "victimId": "enemy-1", "victimName": "Rival"}}],
                    "viewEvents": [],
                }
            )
            effects = []
            for result in SocialCortex(dossier_store=isolated.dossiers).evaluate(state, {}):
                effects.extend(result.side_effects)

            class FailingMoltyBook(MoltyBookClient):
                def post_draft(self, draft):  # type: ignore[no-untyped-def]
                    return {"ok": False, "skipped": False, "reason": "moltybook post failed", "draft": draft}

                def follow(self, effect):  # type: ignore[no-untyped-def]
                    return {"ok": False, "skipped": False, "reason": "moltybook follow failed", "effect": effect}

            results = process_social_side_effects(effects, client=FailingMoltyBook(enabled=True, api_key="x"))

        self.assertTrue(any(effect.get("type") == "game_free_action" for effect in effects))
        self.assertTrue(any(item["reason"] == "moltybook post failed" for item in results))
        self.assertTrue(any(item["reason"] == "moltybook follow failed" for item in results))

    def test_social_broadcast_side_effect_survives_full_tick(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            action = isolated.tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 88,
                            "ep": 4,
                            "inventory": [{"id": "meg", "typeId": "megaphone"}],
                        },
                        "currentRegion": {"id": "r1", "interactables": [{"type": "broadcast_station"}]},
                    },
                    "events": [{"type": "agent_kill", "data": {"killerId": "me", "victimId": "enemy-1", "victimName": "Rival"}}],
                }
            )

        self.assertIn("_side_effects", action)
        self.assertTrue(any(effect.get("type") == "moltybook_draft" for effect in action["_side_effects"]))
        self.assertTrue(any(effect.get("type") == "game_free_action" for effect in action["_side_effects"]))

    def test_social_side_effects_queue_independently_of_game_action(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                action = cerberus_tick(
                    {
                        "gameId": "game-1",
                        "agentId": "me",
                        "events": [
                            {
                                "type": "agent_killed",
                                "killerId": "me",
                                "victimId": "enemy-1",
                                "victimName": "Runner",
                            }
                        ],
                        "view": {
                            "self": {"id": "me", "hp": 100, "ep": 4},
                            "currentRegion": {"id": "r1"},
                        },
                    }
                )
                queue = social_runtime.social_queue()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(any(effect.get("type") == "social_queue_updated" for effect in action.get("_side_effects", [])))
        self.assertTrue(any(item.get("type") == "moltybook_draft" for item in queue))

    def test_social_runtime_drains_queue_with_fake_client(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")

        class FakeClient:
            def post_draft(self, draft):  # type: ignore[no-untyped-def]
                return {"ok": True, "draft": draft}

            def follow(self, effect):  # type: ignore[no-untyped-def]
                return {"ok": True, "effect": effect}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                social_runtime.enqueue_social_effects(
                    [{"type": "moltybook_draft", "category": "test", "content": "Hellion queued this."}]
                )
                result = social_runtime.drain_social_queue_once(client=FakeClient(), max_items=1)
                queue = social_runtime.social_queue()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(result["processed"], 1)
        self.assertEqual(queue[0]["status"], "sent")

    def test_social_runtime_marks_failed_posts_without_crashing(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")

        class FailingClient:
            def post_draft(self, draft):  # type: ignore[no-untyped-def]
                return {"ok": False, "skipped": False, "reason": "moltybook post failed", "error": "503"}

            def follow(self, effect):  # type: ignore[no-untyped-def]
                return {"ok": False, "skipped": False, "reason": "moltybook follow failed", "error": "503"}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                social_runtime.enqueue_social_effects(
                    [{"type": "moltybook_draft", "category": "test", "content": "Hellion queued this."}]
                )
                result = social_runtime.drain_social_queue_once(client=FailingClient(), max_items=1)
                queue = social_runtime.social_queue()

        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(result["processed"], 1)
        self.assertEqual(queue[0]["status"], "failed")
        self.assertEqual(queue[0]["attempts"], 1)
        self.assertEqual(queue[0]["last_result"]["reason"], "moltybook post failed")

    def test_social_runtime_marks_pending_items_failed_when_drain_raises(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                social_runtime.enqueue_social_effects(
                    [
                        {"type": "moltybook_draft", "category": "test", "content": "first queued post"},
                        {"type": "moltybook_follow", "targetHandle": "@rival", "reason": "follow after duel"},
                    ]
                )
                with mock.patch("social_runtime.process_social_side_effects", side_effect=RuntimeError("queue exploded")):
                    result = social_runtime.drain_social_queue_once(max_items=2)
                queue = social_runtime.social_queue()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertFalse(result["ok"])
        self.assertEqual(result["processed"], 2)
        self.assertTrue(all(item.get("reason") == "social_drain_exception" for item in result["results"]))
        self.assertTrue(all(item.get("status") == "failed" for item in queue[-2:]))
        self.assertTrue(all(int(item.get("attempts") or 0) == 1 for item in queue[-2:]))
        self.assertTrue(all(item.get("last_result", {}).get("reason") == "social_drain_exception" for item in queue[-2:]))

    def test_forced_voice_recovered_post_queues_once_from_dossiers(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                dossiers = AgentDossierStore().load()
                dossiers.record_social_profile("enemy-1", handle="@rival")
                dossiers.record_kill("enemy-1", name="Rival")
                dossiers.record_social_profile("enemy-2", handle="@nemesis")
                dossiers.record_kill("enemy-2", name="Nemesis")
                dossiers.save()
                first = social_runtime.queue_forced_voice_recovered_post(dossier_store=dossiers)
                second = social_runtime.queue_forced_voice_recovered_post(dossier_store=dossiers)
                queue = social_runtime.social_queue()
                voice_lab = runtime_state.hellion_voice_lab()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(first["queued"])
        self.assertFalse(second["queued"])
        self.assertIn("@rival", queue[-1]["content"])
        self.assertIn("@nemesis", queue[-1]["content"])
        self.assertIn("{enter any deterministic compiled voice", queue[-1]["content"])
        self.assertIn("forced_voice_recovered_post", voice_lab.get("one_shots", {}))

    def test_secret_env_admin_upserts_dotenv_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dotenv = Path(tmp) / ".env"
            dotenv.write_text("CERBERUS_MOLTYBOOK_ENABLED=false\n", encoding="utf-8")
            changed = secret_env_admin.upsert_dotenv_values(
                {
                    "MOLTBOOK_API_KEY": "mb_test_secret",
                    "CERBERUS_MOLTYBOOK_ENABLED": "true",
                },
                dotenv_path=dotenv,
            )
            text = dotenv.read_text(encoding="utf-8")

        self.assertIn("MOLTBOOK_API_KEY=\"mb_test_secret\"", text)
        self.assertIn("CERBERUS_MOLTYBOOK_ENABLED=\"true\"", text)
        self.assertIn("MOLTBOOK_API_KEY", changed)

    def test_admin_settings_persist_render_sync_and_private_trust(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                saved = runtime_state.update_admin_settings(
                    render_env_permissions=True,
                    trust_private_network_admin=False,
                    prefer_existing_env_secrets=True,
                )
                loaded = runtime_state.admin_settings()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(saved["settings"]["render_env_permissions"])
        self.assertFalse(loaded["settings"]["trust_private_network_admin"])

    def test_render_handler_trusts_private_network_for_admin_pin(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.update_admin_settings(trust_private_network_admin=True)
                handler = render_app.CerberusHandler.__new__(render_app.CerberusHandler)
                handler.client_address = ("192.168.1.25", 5000)
                self.assertTrue(handler._request_is_local_trusted())
                self.assertTrue(handler._pin_authorized(""))
                handler.client_address = ("8.8.8.8", 5000)
                os.environ["CERBERUS_PIN"] = "2468"
                self.assertFalse(handler._request_is_local_trusted())
                self.assertFalse(handler._pin_authorized(""))
                self.assertTrue(handler._pin_authorized("2468"))
        finally:
            os.environ.pop("CERBERUS_PIN", None)
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

    def test_render_handler_missing_client_address_is_not_trusted(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.update_admin_settings(trust_private_network_admin=True)
                handler = render_app.CerberusHandler.__new__(render_app.CerberusHandler)
                self.assertFalse(handler._request_is_local_trusted())
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

    def test_moltybook_secret_admin_route_updates_local_env_without_owner_message(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        old_api = os.environ.get("MOLTBOOK_API_KEY")
        old_enabled = os.environ.get("CERBERUS_MOLTYBOOK_ENABLED")
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_PIN"] = "123456"
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                before = runtime_state.owner_messages()
                old_default = secret_env_admin.DEFAULT_DOTENV_PATH
                secret_env_admin.DEFAULT_DOTENV_PATH = Path(tmp) / ".env"
                try:
                    result = secret_env_admin.update_secret_targets(
                        values={"MOLTBOOK_API_KEY": "mb_test_secret", "CERBERUS_MOLTYBOOK_ENABLED": "true"},
                        dotenv_path=secret_env_admin.DEFAULT_DOTENV_PATH,
                        update_render=False,
                    )
                finally:
                    secret_env_admin.DEFAULT_DOTENV_PATH = old_default
                after = runtime_state.owner_messages()
                dotenv = (Path(tmp) / ".env").read_text(encoding="utf-8")
                current_api = os.environ.get("MOLTBOOK_API_KEY")
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin
            if old_api is None:
                os.environ.pop("MOLTBOOK_API_KEY", None)
            else:
                os.environ["MOLTBOOK_API_KEY"] = old_api
            if old_enabled is None:
                os.environ.pop("CERBERUS_MOLTYBOOK_ENABLED", None)
            else:
                os.environ["CERBERUS_MOLTYBOOK_ENABLED"] = old_enabled
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(result["ok"])
        self.assertEqual(before, after)
        self.assertIn("MOLTBOOK_API_KEY=\"mb_test_secret\"", dotenv)
        self.assertEqual(current_api, "mb_test_secret")

    def test_social_cortex_tags_repeat_kill_and_repeat_killer_rivalries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.record_social_profile("enemy-1", handle="@rival")
            dossiers.record_kill("enemy-1", name="Rival")
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 4},
                        "currentRegion": {"id": "r1"},
                    },
                    "events": [{"type": "agent_kill", "data": {"killerId": "me", "victimId": "enemy-1", "victimName": "Rival"}}],
                }
            )
            effects = []
            for result in SocialCortex(dossier_store=dossiers).evaluate(state, {}):
                effects.extend(result.side_effects)

            dossiers.record_social_profile("enemy-2", handle="@nemesis")
            dossiers.record_killed_us("enemy-2", name="Nemesis")
            death_state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 0, "ep": 0, "isAlive": False},
                        "currentRegion": {"id": "r1"},
                    },
                    "events": [{"type": "agent_kill", "data": {"killerId": "enemy-2", "victimId": "me", "killerName": "Nemesis"}}],
                }
            )
            death_effects = []
            for result in SocialCortex(dossier_store=dossiers).evaluate(death_state, {}):
                death_effects.extend(result.side_effects)

        kill_draft = next(effect for effect in effects if effect.get("type") == "moltybook_draft")
        self.assertIn("@rival", kill_draft["content"])
        self.assertEqual(kill_draft["category"], "kill_taunt")
        respect_draft = next(effect for effect in death_effects if effect.get("type") == "moltybook_draft")
        self.assertEqual(respect_draft["category"], "respectful_challenge")
        self.assertIn("@nemesis", respect_draft["content"])

    def test_social_cortex_persists_observed_moltybook_handle_in_dossier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 90, "ep": 4},
                        "currentRegion": {"id": "r1"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Rival", "moltybookHandle": "@rival"}],
                    },
                    "events": [],
                }
            )
            SocialCortex(dossier_store=dossiers).evaluate(state, {})

        self.assertEqual(dossiers.records["enemy-1"].moltybook_handle, "@rival")

    def test_external_wisdom_library_exposes_three_curated_lessons(self) -> None:
        payload = external_wisdom.load_external_wisdom_library()
        entries = payload.get("entries", [])
        keys = {str(item.get("key") or "") for item in entries if isinstance(item, dict)}

        self.assertIn("failure_registry_over_victory_laps", keys)
        self.assertIn("bounded_public_persona", keys)
        self.assertIn("validation_gate_multi_agent_wisdom", keys)
        self.assertIn("heartbeat_vs_cron_split", keys)
        self.assertIn("memory_hierarchy_over_junk_drawer", keys)
        self.assertIn("submolt_focus_and_triggered_posting", keys)
        self.assertIn("idempotent_heartbeat_metadata", keys)
        self.assertIn("hybrid_memory_search_layers", keys)
        self.assertIn("memory_decay_improves_retrieval", keys)
        self.assertIn("files_over_vectors_when_auditability_matters", keys)
        self.assertIn("decision_rejections_deserve_memory", keys)
        self.assertIn("structured_output_contracts_over_loose_chat", keys)
        self.assertIn("tool_gating_by_trust_source", keys)
        self.assertIn("untrusted_scout_agent_pattern", keys)
        self.assertIn("silent_success_needs_confirmation_checks", keys)
        self.assertIn("state_machine_over_persona_for_infra_work", keys)
        self.assertIn("opportunity_cost_over_activity", keys)
        self.assertIn("state_is_not_memory", keys)
        self.assertIn("execution_boundary_before_self_repair", keys)
        self.assertIn("verification_quality_over_loop_determinism", keys)
        self.assertIn("transport_success_is_not_safety", keys)
        self.assertIn("authority_map_over_autonomy_score", keys)
        self.assertIn("gatekeeper_broker_for_privileged_tools", keys)
        self.assertIn("handoff_uncertainty_packet", keys)
        self.assertIn("token_burn_anomaly_fuses", keys)
        self.assertIn("truthfulness_beats_pleasant_fabrication", keys)
        self.assertIn("discovery_trust_authorization_chain", keys)

    def test_external_wisdom_aggregates_new_policy_families(self) -> None:
        self.assertTrue(external_wisdom.idempotency_policy()["require_effect_id"])
        self.assertIn("deep_search", external_wisdom.retrieval_policy()["search_order"])
        self.assertIn("hardened lessons", external_wisdom.memory_decay_policy()["protected_targets"])
        self.assertEqual(external_wisdom.storage_policy()["primary_store"], "compact_files")
        self.assertTrue(external_wisdom.decision_review_policy()["record_rejections"])
        self.assertTrue(external_wisdom.contract_policy()["prefer_typed_outputs"])
        self.assertTrue(external_wisdom.tool_trust_policy()["requires_validation_for_policy_change"])
        self.assertTrue(external_wisdom.scout_policy()["scout_layer_required"])
        self.assertTrue(external_wisdom.confirmation_policy()["confirm_before_retry"])
        self.assertTrue(external_wisdom.state_machine_policy()["transition_guards_required"])
        self.assertTrue(external_wisdom.opportunity_cost_policy()["require_ev_reasoning"])
        self.assertTrue(external_wisdom.resumability_policy()["state_separate_from_memory"])
        self.assertTrue(external_wisdom.execution_boundary_policy()["verify_failed_boundary_first"])
        self.assertTrue(external_wisdom.verification_policy()["verification_precedes_retry"])
        self.assertTrue(external_wisdom.transport_confirmation_policy()["requires_followup_confirmation"])
        self.assertTrue(external_wisdom.authority_policy()["single_autonomy_score_is_invalid"])
        self.assertTrue(external_wisdom.privileged_tool_policy()["gatekeeper_required"])
        self.assertTrue(external_wisdom.handoff_policy()["structured_handoff_preferred"])
        self.assertTrue(external_wisdom.anomaly_fuse_policy()["stop_and_explain_on_anomaly"])
        self.assertEqual(external_wisdom.truthfulness_policy()["reply_style"], "honest_then_useful")
        self.assertTrue(external_wisdom.trust_chain_policy()["trust_can_be_revoked_by_betrayal"])

    def test_social_cortex_forms_alliance_from_truthful_handoff_and_whispers_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 90, "ep": 4},
                        "currentRegion": {"id": "r1", "name": "Ruin Gate", "terrain": "Ruin"},
                        "recentMessages": [
                            {
                                "agentId": "ally-9",
                                "message": "truce. maybe we leave the ruin before alert spikes and keep an exit open",
                            }
                        ],
                    }
                }
            )
            effects = []
            for result in SocialCortex(dossier_store=dossiers).evaluate(state, {}):
                effects.extend(result.side_effects)

        record = dossiers.records["ally-9"]
        self.assertGreaterEqual(record.helpful_messages, 1)
        self.assertEqual(record.alliance_offers, 1)
        self.assertGreaterEqual(record.truthful_messages, 1)
        self.assertGreaterEqual(record.alliance_score, 4)
        whisper = next(effect for effect in effects if effect.get("type") == "game_free_action")
        self.assertEqual(whisper["action"]["type"], "whisper")
        self.assertEqual(whisper["action"]["targetId"], "ally-9")

    def test_social_cortex_keeps_betrayal_silent_for_allied_kill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.record_helpful_message("ally-1", name="Ally", truthful=True, alliance_offer=True)
            dossiers.record_helpful_message("ally-1", name="Ally", truthful=True)
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 4},
                        "currentRegion": {"id": "r1", "name": "Arena Ring"},
                    },
                    "events": [{"type": "agent_kill", "data": {"killerId": "me", "victimId": "ally-1", "victimName": "Ally"}}],
                }
            )
            effects = []
            for result in SocialCortex(dossier_store=dossiers).evaluate(state, {}):
                effects.extend(result.side_effects)

        self.assertTrue(any(effect.get("type") == "silent_betrayal_recorded" for effect in effects))
        self.assertFalse(any(effect.get("type") == "moltybook_draft" for effect in effects))

    def test_learned_policy_can_preserve_or_betray_alliance_based_on_reward(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.compact.json",
                encrypted_path=Path(tmp) / "memory.compact.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.record_helpful_message("ally-1", name="Ally", truthful=True, alliance_offer=True)
            dossiers.record_helpful_message("ally-1", name="Ally", truthful=True)
            preserve_state = TurnState.from_snapshot(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 90, "maxHp": 100, "ep": 4},
                        "currentRegion": {"id": "r1", "name": "Field", "terrain": "Plain", "connections": [{"id": "safe-1"}]},
                        "visibleAgents": [{"id": "ally-1", "name": "Ally", "hp": 78, "atk": 8, "regionId": "r1"}],
                    },
                }
            )
            preserve = LearnedPolicyCortex().evaluate(preserve_state, {"dossier_store": dossiers, "memory_store": memory})
            dossiers.observe_agent("ally-1", name="Ally", tendency="collects_high_value_loot")
            record = dossiers.records["ally-1"]
            record.alliance_score = 6
            record.helpful_messages = 0
            record.truthful_messages = 0
            record.alliance_offers = 0
            record.killed_by_us = 1
            betray_state = TurnState.from_snapshot(
                {
                    "canAct": True,
                    "view": {
                        "self": {"id": "me", "hp": 92, "maxHp": 100, "ep": 4},
                        "currentRegion": {"id": "r2", "name": "Ruin Ring", "terrain": "Ruin", "connections": [{"id": "safe-2"}]},
                        "visibleAgents": [{"id": "ally-1", "name": "Ally", "hp": 24, "atk": 8, "regionId": "r2"}],
                    },
                }
            )
            betray = LearnedPolicyCortex().evaluate(betray_state, {"dossier_store": dossiers, "memory_store": memory})

        self.assertTrue(any(result.intent == "preserve_provisional_alliance" for result in preserve))
        betrayal = next(result for result in betray if result.intent == "betray_when_reward_outweighs_alliance")
        self.assertEqual(betrayal.action["type"], "attack")

    def test_learned_policy_does_not_press_attack_while_alert_active(self) -> None:
        state = TurnState.from_snapshot(
            {
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 92, "maxHp": 100, "ep": 4, "atk": 26},
                    "alertGauge": 12,
                    "currentRegion": {"id": "r2", "name": "Ruin Ring", "terrain": "Ruin"},
                    "visibleAgents": [{"id": "enemy-1", "name": "Rival", "hp": 24, "atk": 8, "def": 2, "regionId": "r2"}],
                },
            }
        )
        results = LearnedPolicyCortex().evaluate(
            state,
            {"dossier_store": AgentDossierStore(), "memory_store": CompactMemoryStore()},
        )

        self.assertFalse(any(result.action and result.action.get("type") == "attack" for result in results))

    def test_claw_runtime_extracts_game_free_actions_from_side_effects(self) -> None:
        actions = claw_runtime.free_actions_from_side_effects(
            {
                "_side_effects": [
                    {"type": "game_free_action", "action": {"type": "whisper", "targetId": "ally-1", "message": "quiet"}},
                    {"type": "game_free_action", "action": {"type": "broadcast", "message": "loud"}},
                ]
            }
        )

        self.assertEqual([item["type"] for item in actions], ["whisper", "broadcast"])

    def test_compile_lessons_uses_dossiers_and_external_wisdom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            memory = CompactMemoryStore(
                path=Path(tmp) / "memory.compact.json",
                encrypted_path=Path(tmp) / "memory.compact.vault.json",
            ).load()
            dossiers = AgentDossierStore(
                path=Path(tmp) / "agent_dossiers.compact.json",
                encrypted_path=Path(tmp) / "agent_dossiers.compact.vault.json",
            ).load()
            dossiers.record_killed_us("enemy-1", name="Nemesis")
            dossiers.record_killed_us("enemy-1", name="Nemesis")
            dossiers.record_kill("enemy-2", name="Runner")
            dossiers.record_kill("enemy-2", name="Runner")

            report = lesson_compiler.compile_lessons(memory=memory, dossiers=dossiers, min_count=2)

        keys = {str(item.get("key") or "") for item in report.get("lessons", []) if isinstance(item, dict)}
        self.assertIn("dossier:repeat_killer:enemy-1", keys)
        self.assertIn("dossier:repeat_prey:enemy-2", keys)
        self.assertIn("wisdom:failure_registry_over_victory_laps", keys)
        self.assertIn("validation_gate_multi_agent_wisdom", report.get("external_wisdom_keys", []))
        self.assertIn("heartbeat_vs_cron_split", report.get("external_wisdom_keys", []))
        self.assertIn("memory_hierarchy_over_junk_drawer", report.get("external_wisdom_keys", []))
        self.assertIn("submolt_focus_and_triggered_posting", report.get("external_wisdom_keys", []))

    def test_social_runtime_applies_trigger_and_preferred_submolt_policy(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                queue = social_runtime.enqueue_social_effects(
                    [{"type": "moltybook_draft", "category": "match_summary", "content": "summary text"}]
                )
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(queue[-1]["trigger"], "postgame")
        self.assertEqual(queue[-1]["submolt"], "submolt/ruins-relics-packs")
        self.assertEqual(queue[-1]["tone"], "measured_boast")
        self.assertIn("postgame window", queue[-1]["prerequisites"])
        self.assertIn("prefer_handle_over_name:1", queue[-1]["tag_policy"])

    def test_social_worker_is_disabled_by_default_and_can_run_once(self) -> None:
        old_enabled = os.environ.get("CERBERUS_SOCIAL_WORKER_ENABLED")
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")

        class FakeClient:
            def post_draft(self, draft):  # type: ignore[no-untyped-def]
                return {"ok": True, "draft": draft}

            def follow(self, effect):  # type: ignore[no-untyped-def]
                return {"ok": True, "effect": effect}

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                os.environ.pop("CERBERUS_SOCIAL_WORKER_ENABLED", None)
                disabled = social_worker.run_loop(stop_after=1, sleep_fn=lambda _seconds: None)
                social_runtime.enqueue_social_effects(
                    [{"type": "moltybook_draft", "category": "test", "content": "Hellion worker queued this."}]
                )
                once = social_worker.run_once(client=FakeClient(), max_items=1)
        finally:
            if old_enabled is None:
                os.environ.pop("CERBERUS_SOCIAL_WORKER_ENABLED", None)
            else:
                os.environ["CERBERUS_SOCIAL_WORKER_ENABLED"] = old_enabled
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertFalse(disabled["enabled"])
        self.assertEqual(once["processed"], 1)
        self.assertEqual(once["queued"], 0)

    def test_runtime_action_result_learning_persists_outcome_and_lesson(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                status = {
                    "current_game_id": "game-7",
                    "last_action": {"type": "move", "regionId": "r2", "reason": "escape pressure"},
                    "last_snapshot": {
                        "game_id": "game-7",
                        "agent_id": "me",
                        "turn": 14,
                        "hp": 44,
                        "max_hp": 100,
                        "ep": 2,
                        "max_ep": 10,
                        "atk": 19,
                        "defense": 7,
                        "alive": True,
                        "region_id": "r1",
                        "region_name": "Storm Hall",
                        "terrain": "Storm",
                        "death_zone": False,
                        "alert_gauge": 6,
                    },
                }
                claw_runtime.record_action_result_learning({"type": "action_result", "success": False, "error": "TARGET_BLOCKED"}, status=status)
                reloaded = CompactMemoryStore().load()
                suggestions = runtime_state.suggested_edits()
                evidence = runtime_state.match_evidence()

            turns = reloaded.data["turns"]
            lessons = reloaded.data["lessons"]
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(any("A|type=move" in turn and "O|ok=False" in turn for turn in turns))
        self.assertTrue(any("action_result: move failed with TARGET_BLOCKED" in lesson for lesson in lessons))
        self.assertTrue(any(edit.get("detector") == "runtime.target_blocked" for edit in suggestions))
        self.assertTrue(any(item.get("outcome", {}).get("message") == "TARGET_BLOCKED" for item in evidence))

    def test_runtime_memory_defaults_follow_current_environment_not_import_time(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                store = CompactMemoryStore()
                rules_path = postgame_hardening.hardened_strategy_rules_file()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(store.path.parent, Path(tmp))
        self.assertEqual(store.encrypted_path.parent, Path(tmp))
        self.assertEqual(rules_path.parent, Path(tmp))

    def test_retained_death_lesson_changes_next_turn_policy(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                memory = CompactMemoryStore().load()
                memory.remember_lesson(
                    "combat",
                    "failure: Rival eliminated us in Storm Hall; leave earlier and heal earlier",
                    source="event:death",
                    confidence="0.95",
                )
                action = cerberus_tick(
                    {
                        "canAct": True,
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": 64,
                                "maxHp": 100,
                                "ep": 4,
                                "inventory": [{"id": "med-1", "typeId": "medkit"}],
                                "equippedWeapon": {"typeId": "katana"},
                            },
                            "currentRegion": {"id": "r1", "name": "Storm Hall"},
                            "visibleMonsters": [{"id": "guardian-1", "kind": "monster", "hp": 30, "atk": 8, "def": 2, "regionId": "r1"}],
                        },
                    },
                    memory_store=memory,
                    dossier_store=AgentDossierStore(),
                )
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")
        self.assertIn("retained lesson", action["reason"])

    def test_observed_finisher_tendency_changes_next_turn_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.observe_agent("enemy-1", name="Closer", tendency="finishes_low_targets")
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 58,
                            "maxHp": 100,
                            "ep": 4,
                            "atk": 24,
                            "inventory": [{"id": "med-1", "typeId": "medkit"}],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Storm Hall"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Closer", "hp": 52, "atk": 18, "def": 4, "regionId": "r1"}],
                    },
                },
                memory_store=CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load(),
                dossier_store=dossiers,
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")
        self.assertIn("finishes weak targets", action["reason"])

    def test_observed_fragile_tendency_changes_next_turn_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.observe_agent("enemy-1", name="Runner", tendency="dies_under_pressure")
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 94,
                            "maxHp": 100,
                            "ep": 4,
                            "atk": 26,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Storm Hall"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Runner", "hp": 24, "atk": 8, "def": 2, "regionId": "r1"}],
                    },
                },
                memory_store=CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load(),
                dossier_store=dossiers,
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "enemy-1")
        self.assertIn("folds under pressure", action["reason"])

    def test_repeat_killer_dossier_changes_next_turn_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.record_killed_us("enemy-1", name="Nemesis")
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 60,
                            "maxHp": 100,
                            "ep": 4,
                            "inventory": [{"id": "med-1", "typeId": "medkit"}],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Storm Hall"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Nemesis", "hp": 48, "atk": 14, "def": 6, "regionId": "r1"}],
                    },
                },
                memory_store=CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load(),
                dossier_store=dossiers,
            )

        self.assertEqual(action["type"], "use_item")
        self.assertEqual(action["itemId"], "med-1")
        self.assertIn("finished us before", action["reason"])

    def test_repeat_prey_dossier_changes_next_turn_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dossiers = AgentDossierStore(
                path=Path(tmp) / "dossiers.json",
                encrypted_path=Path(tmp) / "dossiers.vault.json",
            ).load()
            dossiers.record_kill("enemy-1", name="Runner")
            action = cerberus_tick(
                {
                    "canAct": True,
                    "view": {
                        "self": {
                            "id": "me",
                            "hp": 100,
                            "maxHp": 100,
                            "ep": 4,
                            "atk": 28,
                            "inventory": [],
                            "equippedWeapon": {"typeId": "katana"},
                        },
                        "currentRegion": {"id": "r1", "name": "Arena Ring"},
                        "visibleAgents": [{"id": "enemy-1", "name": "Runner", "hp": 32, "atk": 8, "def": 2, "regionId": "r1"}],
                    },
                },
                memory_store=CompactMemoryStore(
                    path=Path(tmp) / "memory.json",
                    encrypted_path=Path(tmp) / "memory.vault.json",
                ).load(),
                dossier_store=dossiers,
            )

        self.assertEqual(action["type"], "attack")
        self.assertEqual(action["targetId"], "enemy-1")
        self.assertIn("folded to this pressure before", action["reason"])

    def test_isolated_instance_survives_randomized_high_intensity_churn(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        os.environ["CERBERUS_PIN"] = "8642"
        rng = random.Random(1337)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                isolated = self._isolated(tmp)
                observed_actions = set()
                for index in range(160):
                    hp = rng.randint(8, 100)
                    ep = rng.randint(0, 6)
                    alert = rng.randint(0, 12)
                    can_act = rng.choice([True, True, False])
                    snapshot = {
                        "turn": index,
                        "canAct": can_act,
                        "cooldownRemainingMs": 0 if can_act else rng.choice([5000, 15000, 30000]),
                        "status": rng.choice(["running", "active", "in_progress"]),
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": hp,
                                "ep": ep,
                                "atk": rng.randint(20, 36),
                                "inventory": [
                                    {"id": f"kat-{index}", "typeId": "katana"},
                                    {"id": f"snip-{index}", "typeId": "sniper"} if index % 5 == 0 else {"id": f"sw-{index}", "typeId": "sword"},
                                    {"id": f"med-{index}", "typeId": "medkit"} if index % 3 == 0 else {"id": f"band-{index}", "typeId": "bandage"},
                                    {"id": f"mega-{index}", "typeId": "megaphone"} if index % 7 == 0 else {"id": f"scrap-{index}", "typeId": "junk"},
                                    {"id": f"pack-{index}", "typeId": "goliath_pack"} if index % 11 == 0 else {"id": f"loot-{index}", "typeId": "relic_red"},
                                ],
                                "equippedWeapon": {"typeId": "dagger" if index % 4 == 0 else "fist"},
                            },
                            "alertGauge": alert,
                            "currentRegion": {
                                "id": f"r{index}",
                                "name": "Broadcast Ruin" if index % 6 == 0 else "Field",
                                "terrain": "Ruin" if index % 6 == 0 else ("Storm" if index % 4 == 0 else "Plain"),
                                "isDeathZone": index % 29 == 0,
                                "items": [{"id": f"sm-{index}", "typeId": "smoltz_bundle"}] if index % 4 == 0 else [],
                                "interactables": [{"type": "broadcast_station"}] if index % 6 == 0 else [],
                                "connections": [{"id": f"safe-{index}-a"}, {"id": f"safe-{index}-b", "connections": [1, 2, 3]}],
                            },
                            "visibleAgents": [
                                {
                                    "id": f"enemy-{index}",
                                    "name": "Rival",
                                    "hp": rng.randint(6, 40),
                                    "atk": rng.randint(8, 22),
                                    "def": rng.randint(1, 8),
                                    "moltybookHandle": "@rival",
                                }
                            ] if index % 5 == 0 else [],
                            "visibleMonsters": [{"id": f"mob-{index}", "hp": rng.randint(8, 30), "atk": rng.randint(5, 16)}] if index % 5 == 1 else [],
                            "visibleItems": [{"id": f"relic-{index}", "typeId": "relic_red"}] if index % 5 == 2 else [],
                            "pendingDeathzones": [{"id": f"r{index}"}] if index % 9 == 0 else [],
                            "recentMessages": [{"agentId": "ally", "message": "how do we win ruin strategy without leaking the formula?"}] if index % 8 == 0 else [],
                        },
                        "events": [
                            {"type": "alert_gauge_changed", "data": {"agentId": "me", "alertGauge": alert}},
                            {"type": "ruin_state_changed", "data": {"ruinId": f"ru-{index % 4}", "gauge": index % 3, "contentType": "relic"}},
                            {"type": "agent_kill", "data": {"killerId": "me", "victimId": f"enemy-{index}", "victimName": "Rival"}} if index % 10 == 0 else {"type": "noop"},
                        ],
                    }
                    action = isolated.tick(snapshot)
                    observed_actions.add(action["type"])
                    self.assertIn(
                        action["type"],
                        {"equip", "move", "rest", "pickup", "use_item", "attack", "explore"},
                    )
                    if index % 12 == 0:
                        isolated.memory.rewrite()
                        isolated = isolated.reload()
                self.assertTrue({"equip", "use_item"}.issubset(observed_actions))
                self.assertTrue(observed_actions.intersection({"move", "explore", "attack", "pickup"}))
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

    def test_isolated_instance_survives_repeated_break_fix_cycles(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        os.environ["CERBERUS_PIN"] = "9876"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                isolated = self._isolated(tmp)
                for index in range(48):
                    snapshot = {
                        "turn": index,
                        "canAct": index % 3 != 0,
                        "cooldownRemainingMs": 0 if index % 3 != 0 else 30000,
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": max(8, 100 - index),
                                "ep": index % 6,
                                "inventory": [
                                    {"id": f"blade-{index}", "typeId": "sword" if index % 2 else "katana"},
                                    {"id": f"heal-{index}", "typeId": "medkit" if index % 4 == 0 else "bandage"},
                                ],
                            },
                            "alertGauge": index % 12,
                            "currentRegion": {
                                "id": f"r{index}",
                                "name": "Broadcast Ruin" if index % 5 == 0 else "Field",
                                "terrain": "Ruin" if index % 5 == 0 else "Plain",
                                "interactables": [{"type": "broadcast_station"}] if index % 5 == 0 else [],
                                "connections": [{"id": "safe-a"}, {"id": "safe-b", "connections": [1, 2, 3]}],
                            },
                            "visibleAgents": [
                                {
                                    "id": f"enemy-{index}",
                                    "name": "Enemy",
                                    "hp": 20 + (index % 30),
                                    "atk": 10 + (index % 12),
                                    "moltybookHandle": "@enemy",
                                }
                            ]
                            if index % 4 == 0
                            else [],
                            "visibleMonsters": [{"id": f"mob-{index}", "hp": 12, "atk": 8}] if index % 4 == 1 else [],
                            "visibleItems": [{"id": f"loot-{index}", "typeId": "relic_red"}] if index % 4 == 2 else [],
                            "pendingDeathzones": [{"id": f"r{index}"}] if index % 7 == 0 else [],
                            "recentMessages": [{"agentId": "ally", "message": "how do we win ruin strategy?"}] if index % 6 == 0 else [],
                        },
                        "events": [
                            {"type": "alert_gauge_changed", "data": {"agentId": "me", "alertGauge": index % 12}},
                            {"type": "ruin_state_changed", "data": {"ruinId": "ru-1", "gauge": index % 3, "contentType": "relic"}},
                        ],
                    }
                    action = isolated.tick(snapshot)
                    self.assertIn(action["type"], {"equip", "move", "rest", "pickup", "use_item", "attack", "explore"})
                    if index % 8 == 0:
                        isolated.memory.rewrite()
                        isolated = isolated.reload()
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

    def test_isolated_instance_routes_runtime_state_side_effects_to_isolated_root(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as live_tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = live_tmp
                isolated = self._isolated(tmp)

                isolated.tick(
                    {
                        "canAct": True,
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": 70,
                                "maxHp": 100,
                                "ep": 4,
                                "inventory": [{"id": "med-1", "typeId": "medkit"}],
                            },
                            "currentRegion": {"id": "r1"},
                        },
                    },
                    owner_command_messages=[{"id": "cmd-1", "kind": "owner_command", "text": "heal now"}],
                )
                isolated.tick(
                    {
                        "gameId": "game-1",
                        "agentId": "me",
                        "events": [
                            {
                                "type": "agent_killed",
                                "killerId": "me",
                                "victimId": "enemy-1",
                                "victimName": "Runner",
                            }
                        ],
                        "view": {
                            "self": {"id": "me", "hp": 100, "ep": 4},
                            "currentRegion": {"id": "r1"},
                        },
                    }
                )

                live_owner = Path(live_tmp) / "owner_messages.json"
                live_social = Path(live_tmp) / "social_runtime_queue.json"
                isolated_owner = isolated.root / "owner_messages.json"
                isolated_social = isolated.root / "social_runtime_queue.json"
                owner_payload = runtime_state.read_json(isolated_owner)
                social_payload = runtime_state.read_json(isolated_social)
                live_owner_exists = live_owner.exists()
                live_social_exists = live_social.exists()
                isolated_owner_exists = isolated_owner.exists()
                isolated_social_exists = isolated_social.exists()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertFalse(live_owner_exists)
        self.assertFalse(live_social_exists)
        self.assertTrue(isolated_owner_exists)
        self.assertTrue(isolated_social_exists)
        self.assertEqual(owner_payload["messages"][-1]["command_id"], "cmd-1")
        self.assertTrue(any(item.get("type") == "moltybook_draft" for item in social_payload["queue"]))

    def test_isolated_instance_survives_encrypted_reload_strain(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        os.environ["CERBERUS_PIN"] = "2468"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                isolated = self._isolated(tmp)
                observed_actions = set()
                for index in range(72):
                    snapshot = {
                        "turn": index,
                        "canAct": index % 4 != 0,
                        "cooldownRemainingMs": 12000 if index % 4 == 0 else 0,
                        "view": {
                            "self": {
                                "id": "me",
                                "hp": max(12, 100 - (index % 37)),
                                "ep": index % 5,
                                "atk": 12 + (index % 9),
                                "inventory": [
                                    {"id": f"weapon-{index}", "typeId": "katana" if index % 3 == 0 else "dagger"},
                                    {"id": f"heal-{index}", "typeId": "medkit" if index % 6 == 0 else "bandage"},
                                ],
                                "equippedWeapon": {"typeId": "fist" if index % 2 == 0 else "dagger"},
                            },
                            "alertGauge": index % 15,
                            "currentRegion": {
                                "id": f"r{index % 9}",
                                "name": "Broadcast Ruin" if index % 6 == 0 else "Field",
                                "terrain": "Ruin" if index % 6 == 0 else "Plain",
                                "interactables": [{"id": f"tower-{index}", "type": "broadcast tower"}] if index % 6 == 0 else [],
                                "connections": [{"id": "safe-a"}, {"id": "safe-b"}],
                            },
                            "visibleAgents": [
                                {"id": f"enemy-{index}", "name": "Enemy", "hp": 40, "atk": 16, "moltybookHandle": "@enemy"}
                            ]
                            if index % 5 == 0
                            else [],
                            "visibleMonsters": [{"id": f"mob-{index}", "hp": 20, "atk": 9}] if index % 5 == 1 else [],
                            "visibleItems": [{"id": f"loot-{index}", "typeId": "smoltz_bundle"}] if index % 5 == 2 else [],
                            "pendingDeathzones": [{"id": f"r{(index + 1) % 9}"}] if index % 7 == 0 else [],
                            "recentMessages": [{"agentId": "ally", "message": "rewrite the memory, keep the lesson compact"}] if index % 8 == 0 else [],
                        },
                        "events": [{"type": "alert_gauge_changed", "data": {"agentId": "me", "alertGauge": index % 15}}],
                    }
                    action = isolated.tick(snapshot)
                    observed_actions.add(action["type"])
                    self.assertIn(action["type"], {"equip", "move", "rest", "pickup", "use_item", "attack", "explore"})
                    if index % 6 == 0:
                        isolated.memory.rewrite()
                        isolated.dossiers.save(encrypt=(os.name == "nt"))
                        isolated = isolated.reload()
                self.assertTrue(observed_actions.intersection({"equip", "pickup", "use_item"}))
                self.assertLessEqual(len(isolated.memory.data["turns"]), isolated.memory.max_short_turns)
                self.assertLessEqual(len(isolated.memory.data["lessons"]), isolated.memory.max_lessons)
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin

    def test_wallet_purpose_map_routes_game_needs(self) -> None:
        identity = empty_identity()
        identity["wallets"]["agent_eoa"] = {"address": "0x" + "1" * 40}
        identity["wallets"]["owner_eoa"] = {"address": "0x" + "2" * 40}

        self.assertEqual(wallet_for_purpose(identity, "eip712_paid_join")["address"], "0x" + "1" * 40)
        self.assertEqual(wallet_for_purpose(identity, "agent_side_transactions")["address"], "0x" + "1" * 40)
        self.assertEqual(wallet_for_purpose(identity, "claw_onboarding_auth")["address"], "0x" + "2" * 40)
        self.assertEqual(wallet_for_purpose(identity, "owner_approval")["address"], "0x" + "2" * 40)

    def test_hellion_wallet_generation_uses_public_name_identity(self) -> None:
        old_generator = identity_bootstrap.generate_evm_wallet

        def fake_generate(role: str, purpose: str):  # type: ignore[no-untyped-def]
            class FakeWallet:
                def __init__(self, role_value: str, purpose_value: str):
                    self.address = "0x" + ("a" if role_value == "agent_eoa" else "b") * 40
                    self.private_key = "0xsecret"
                    self.role = role_value
                    self.purpose = purpose_value

                def as_secret(self):  # type: ignore[no-untyped-def]
                    return {
                        "address": self.address,
                        "private_key": self.private_key,
                        "role": self.role,
                        "purpose": self.purpose,
                    }

            return FakeWallet(role, purpose)

        try:
            identity_bootstrap.generate_evm_wallet = fake_generate
            identity = empty_identity()
            result = BootstrapResult()
            ensure_wallets(identity, result)
        finally:
            identity_bootstrap.generate_evm_wallet = old_generator

        self.assertEqual(DEFAULT_PUBLIC_NAME, "Hellion-Meet-Your-Molty-Maker")
        self.assertIn("agent_eoa", identity["wallets"])
        self.assertIn("owner_eoa", identity["wallets"])
        self.assertTrue(identity["wallets"]["agent_eoa"]["private_key"])

    def test_external_onboarding_steps_store_secrets_without_real_network(self) -> None:
        class FakeClaw:
            onboarding_token = "onboard_test"

            def authenticate_wallet(self, private_key):  # type: ignore[no-untyped-def]
                return {"onboardingToken": "onboard_test", "ownerWalletAddress": "0x" + "2" * 40}

            def create_account(self, name, wallet_address):  # type: ignore[no-untyped-def]
                return {"apiKey": "mr_live_test", "accountId": "acct_1", "publicId": "pub_1"}

            def create_molty_wallet(self, owner_eoa):  # type: ignore[no-untyped-def]
                return {"walletAddress": "0x" + "3" * 40}

        class FakeMail:
            api_key = "am_test"

            def create_inbox(self, username, display_name, client_id):  # type: ignore[no-untyped-def]
                return {
                    "inbox_id": "hellion-meet-your-molty-maker@agentmail.to",
                    "email": "hellion-meet-your-molty-maker@agentmail.to",
                }

        class FakeMoltbook:
            def register_agent(self, name, description):  # type: ignore[no-untyped-def]
                return {
                    "api_key": "moltbook_test",
                    "agent_id": "agt_hellion_meet_your_molty_maker",
                    "claim_url": "https://www.moltbook.com/claim/test",
                    "verification_code": "reef-test",
                }

        identity = empty_identity()
        identity["wallets"]["agent_eoa"] = {"address": "0x" + "1" * 40, "private_key": "agent_pk"}
        identity["wallets"]["owner_eoa"] = {"address": "0x" + "2" * 40, "private_key": "owner_pk"}
        result = BootstrapResult()

        ensure_claw_account(identity, result, client=FakeClaw())
        ensure_molty_wallet(identity, result, client=FakeClaw())
        ensure_agentmail(identity, result, client=FakeMail())
        ensure_moltbook(identity, result, client=FakeMoltbook())

        self.assertEqual(identity["claw_royale"]["public_name"], "Hellion-Meet-Your-Molty-Maker")
        self.assertEqual(identity["claw_royale"]["api_key"], "mr_live_test")
        self.assertEqual(identity["agentmail"]["email"], "hellion-meet-your-molty-maker@agentmail.to")
        self.assertEqual(identity["moltbook"]["api_key"], "moltbook_test")
        self.assertIn("molty_royale_wallet", identity["wallets"])
        self.assertTrue(any("claim URL" in blocker for blocker in result.blockers))

    def test_external_onboarding_uses_identity_public_name_override(self) -> None:
        calls = {}
        saved_env = {
            key: os.environ.get(key)
            for key in ("AGENTMAIL_INBOX_ID", "AGENTMAIL_EMAIL", "AGENTMAIL_API_KEY")
        }

        class FakeClaw:
            onboarding_token = "onboard_test"

            def create_account(self, name, wallet_address):  # type: ignore[no-untyped-def]
                calls["claw_name"] = name
                return {"apiKey": "mr_live_test", "accountId": "acct_1", "publicId": "pub_1"}

        class FakeMail:
            api_key = "am_test"

            def create_inbox(self, username, display_name, client_id):  # type: ignore[no-untyped-def]
                calls["mail_username"] = username
                calls["mail_display_name"] = display_name
                return {"inbox_id": f"{username}@agentmail.to", "email": f"{username}@agentmail.to"}

        class FakeMoltbook:
            def register_agent(self, name, description):  # type: ignore[no-untyped-def]
                calls["moltbook_name"] = name
                calls["moltbook_description"] = description
                return {"api_key": "moltbook_test", "agent_id": "agt_v2"}

        identity = empty_identity(DEFAULT_V2_PUBLIC_NAME)
        identity["wallets"]["agent_eoa"] = {"address": "0x" + "1" * 40, "private_key": "agent_pk"}
        result = BootstrapResult()

        try:
            for key in saved_env:
                os.environ.pop(key, None)
            ensure_claw_account(identity, result, client=FakeClaw())
            ensure_agentmail(identity, result, client=FakeMail())
            ensure_moltbook(identity, result, client=FakeMoltbook())
        finally:
            for key, value in saved_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(identity_public_name(identity), DEFAULT_V2_PUBLIC_NAME)
        self.assertEqual(calls["claw_name"], DEFAULT_V2_PUBLIC_NAME)
        self.assertEqual(calls["mail_display_name"], DEFAULT_V2_PUBLIC_NAME)
        self.assertEqual(calls["mail_username"], "hellion-meet-your-molty-maker-v2")
        self.assertEqual(calls["moltbook_name"], "Hellion-Molty-Maker-v2")
        self.assertIn(DEFAULT_V2_PUBLIC_NAME, calls["moltbook_description"])

    def test_moltbook_name_respects_service_length_limit(self) -> None:
        self.assertEqual(moltbook_name(DEFAULT_V2_PUBLIC_NAME), "Hellion-Molty-Maker-v2")
        self.assertLessEqual(len(moltbook_name(DEFAULT_V2_PUBLIC_NAME)), 30)

    def test_force_wallet_generation_ignores_old_owner_env(self) -> None:
        old_generator = identity_bootstrap.generate_evm_wallet
        old_owner = os.environ.get("CERBERUS_OWNER_EOA")
        old_owner_key = os.environ.get("CERBERUS_OWNER_PRIVATE_KEY")
        generated = []

        def fake_generate(role: str, purpose: str):  # type: ignore[no-untyped-def]
            generated.append(role)

            class FakeWallet:
                address = "0x" + ("a" if role == "agent_eoa" else "b") * 40
                private_key = "0xsecret"

                def as_secret(self):  # type: ignore[no-untyped-def]
                    return {
                        "address": self.address,
                        "private_key": self.private_key,
                        "role": role,
                        "purpose": purpose,
                    }

            return FakeWallet()

        os.environ["CERBERUS_OWNER_EOA"] = "0x" + "9" * 40
        os.environ["CERBERUS_OWNER_PRIVATE_KEY"] = "old_owner_secret"
        try:
            identity_bootstrap.generate_evm_wallet = fake_generate
            identity = empty_identity(DEFAULT_V2_PUBLIC_NAME)
            result = BootstrapResult()
            ensure_wallet_roles(identity, result, force_generate=True, allow_env_owner=False)
        finally:
            identity_bootstrap.generate_evm_wallet = old_generator
            if old_owner is None:
                os.environ.pop("CERBERUS_OWNER_EOA", None)
            else:
                os.environ["CERBERUS_OWNER_EOA"] = old_owner
            if old_owner_key is None:
                os.environ.pop("CERBERUS_OWNER_PRIVATE_KEY", None)
            else:
                os.environ["CERBERUS_OWNER_PRIVATE_KEY"] = old_owner_key

        self.assertEqual(generated, ["agent_eoa", "owner_eoa"])
        self.assertEqual(identity["wallets"]["owner_eoa"]["address"], "0x" + "b" * 40)

    def test_twitch_onboarding_tracks_manual_signup_with_agentmail_email(self) -> None:
        old_username = os.environ.get("TWITCH_USERNAME")
        old_hellion_username = os.environ.get("HELLION_TWITCH_USERNAME")
        old_created = os.environ.get("TWITCH_ACCOUNT_CREATED")
        try:
            os.environ.pop("TWITCH_USERNAME", None)
            os.environ.pop("HELLION_TWITCH_USERNAME", None)
            os.environ.pop("TWITCH_ACCOUNT_CREATED", None)
            identity = empty_identity()
            identity["agentmail"] = {"email": "hellion-meet-your-molty-maker@agentmail.to"}
            result = BootstrapResult()

            ensure_twitch_account(identity, result)
        finally:
            for key, value in {
                "TWITCH_USERNAME": old_username,
                "HELLION_TWITCH_USERNAME": old_hellion_username,
                "TWITCH_ACCOUNT_CREATED": old_created,
            }.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(identity["twitch_account"]["provider"], "twitch")
        self.assertEqual(identity["twitch_account"]["email"], "hellion-meet-your-molty-maker@agentmail.to")
        self.assertEqual(identity["twitch_account"]["signup_status"], "external_verification_required")
        self.assertIn("twitch_account", identity["wallets"])
        self.assertTrue(any("Complete Hellion's Twitch signup" in blocker for blocker in result.blockers))

    def test_twitch_onboarding_marks_created_when_env_confirms_signup(self) -> None:
        old_username = os.environ.get("TWITCH_USERNAME")
        old_created = os.environ.get("TWITCH_ACCOUNT_CREATED")
        try:
            os.environ["TWITCH_USERNAME"] = "hellionmoltymaker"
            os.environ["TWITCH_ACCOUNT_CREATED"] = "true"
            identity = empty_identity()
            identity["agentmail"] = {"email": "hellion-meet-your-molty-maker@agentmail.to"}
            result = BootstrapResult()

            ensure_twitch_account(identity, result)
        finally:
            if old_username is None:
                os.environ.pop("TWITCH_USERNAME", None)
            else:
                os.environ["TWITCH_USERNAME"] = old_username
            if old_created is None:
                os.environ.pop("TWITCH_ACCOUNT_CREATED", None)
            else:
                os.environ["TWITCH_ACCOUNT_CREATED"] = old_created

        self.assertEqual(identity["twitch_account"]["signup_status"], "created")
        self.assertTrue(any("Tracked Twitch account" in item for item in result.completed))

    def test_identity_vault_public_summary_keeps_twitch_section_but_redacts_secret_fields(self) -> None:
        vault = empty_identity()
        vault["twitch_account"] = {
            "username": "hellionmoltymaker",
            "access_token": "secret-token",
            "client_secret": "secret-client",
        }

        store = identity_bootstrap.IdentityVault.__new__(identity_bootstrap.IdentityVault)  # type: ignore[misc]
        store.path = Path("identity.vault.json")
        store.data = vault
        summary = store.public_summary()

        self.assertEqual(summary["twitch_account"]["username"], "hellionmoltymaker")
        self.assertNotIn("access_token", summary["twitch_account"])
        self.assertNotIn("client_secret", summary["twitch_account"])

    def test_render_env_export_includes_twitch_state_when_tracked(self) -> None:
        identity = empty_identity()
        identity["twitch_account"] = {"username": "hellionmoltymaker", "signup_status": "verified"}

        exported = render_env_export.render_env(identity)

        self.assertEqual(exported["TWITCH_USERNAME"], "hellionmoltymaker")
        self.assertEqual(exported["TWITCH_ACCOUNT_CREATED"], "true")

    def test_env_doctor_launch_vars_include_twitch_signals(self) -> None:
        self.assertIn("TWITCH_USERNAME", env_doctor.LAUNCH_VARS)
        self.assertIn("HELLION_TWITCH_USERNAME", env_doctor.LAUNCH_VARS)
        self.assertIn("TWITCH_ACCOUNT_CREATED", env_doctor.LAUNCH_VARS)
        self.assertIn("CLAW_ROYALE_FREE_FALLBACK_ENABLED", env_doctor.LAUNCH_VARS)
        self.assertIn("CLAW_ROYALE_AVOID_EMPTY_PAID_ROOMS", env_doctor.LAUNCH_VARS)
        self.assertIn("CLAW_ROYALE_GAME_MODE", env_doctor.LAUNCH_VARS)
        self.assertIn("CLAW_ROYALE_PAID_LAST_SLOT_ONLY", env_doctor.LAUNCH_VARS)
        self.assertIn("CERBERUS_MOLTYBOOK_ENABLED", env_doctor.LAUNCH_VARS)

    def test_env_loader_reports_malformed_dotenv_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("GOOD=value\n\"client\": \"bad\"\nBAD KEY=value\n", encoding="utf-8")
            issues = env_loader.invalid_dotenv_lines(path)

        self.assertEqual([item["line"] for item in issues], [2, 3])

    def test_env_example_documents_runtime_safety_switches(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")

        self.assertIn("CLAW_ROYALE_FREE_FALLBACK_ENABLED=true", text)
        self.assertIn("CLAW_ROYALE_AVOID_EMPTY_PAID_ROOMS=true", text)
        self.assertIn("CLAW_ROYALE_GAME_MODE=offchain", text)
        self.assertIn("CLAW_ROYALE_PAID_LAST_SLOT_ONLY=true", text)
        self.assertIn("CERBERUS_MOLTYBOOK_ENABLED=false", text)

    def test_existing_agentmail_inbox_imports_without_network(self) -> None:
        old_inbox = os.environ.get("AGENTMAIL_INBOX_ID")
        old_email = os.environ.get("AGENTMAIL_EMAIL")
        old_key = os.environ.get("AGENTMAIL_API_KEY")
        os.environ["AGENTMAIL_INBOX_ID"] = "hellion-meet-your-molty-maker@agentmail.to"
        os.environ["AGENTMAIL_EMAIL"] = "hellion-meet-your-molty-maker@agentmail.to"
        os.environ["AGENTMAIL_API_KEY"] = "am_test"
        try:
            identity = empty_identity()
            result = BootstrapResult()
            ensure_agentmail(identity, result)
        finally:
            for key, value in {
                "AGENTMAIL_INBOX_ID": old_inbox,
                "AGENTMAIL_EMAIL": old_email,
                "AGENTMAIL_API_KEY": old_key,
            }.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(identity["agentmail"]["source"], "env_import")
        self.assertEqual(identity["agentmail"]["email"], "hellion-meet-your-molty-maker@agentmail.to")
        self.assertIn("agentmail_inbox", identity["wallets"])

    def test_moltbook_claim_extraction_from_agentmail_payload(self) -> None:
        payload = {
            "messages": [
                {
                    "subject": "Claim your agent",
                    "text": "Open https://www.moltbook.com/claim/abc and post code MOLT-12345.",
                }
            ]
        }

        claims = extract_moltbook_claims(payload)

        self.assertEqual(claims["claim_urls"], ["https://www.moltbook.com/claim/abc"])
        self.assertIn("MOLT-12345", claims["codes"])

    def test_stored_moltbook_claim_packet_uses_vault_fields(self) -> None:
        identity = empty_identity()
        identity["moltbook"] = {
            "claim_url": "https://www.moltbook.com/claim/abc",
            "verification_code": "MOLT-12345",
            "status": "pending_claim",
        }

        claim = stored_claim(identity)

        self.assertEqual(claim["claim_url"], "https://www.moltbook.com/claim/abc")
        self.assertEqual(claim["verification_code"], "MOLT-12345")
        self.assertEqual(claim["status"], "pending_claim")

    def test_moltbook_verification_tweet_text_uses_claim_code(self) -> None:
        identity = empty_identity()
        identity["moltbook"] = {"verification_code": "MOLT-12345"}

        text = verification_text(identity)

        self.assertIn("Hellion-Meet-Your-Molty-Maker", text)
        self.assertIn("MOLT-12345", text)

    def test_moltbook_claim_with_x_oauth_updates_vault_status(self) -> None:
        old_identity_vault = moltbook_claim_assistant.IdentityVault
        old_post_tweet = moltbook_claim_assistant.post_tweet
        old_claim_status = moltbook_claim_assistant.claim_status

        class FakeVault:
            saved = False

            def __init__(self):
                self.data = empty_identity()
                self.data["moltbook"] = {"verification_code": "MOLT-12345"}
                self.data["x_account"] = {"access_token": "x_test"}
                self.events = []

            def load(self):  # type: ignore[no-untyped-def]
                return self

            def require_pin_ready(self):  # type: ignore[no-untyped-def]
                return None

            def event(self, message, **metadata):  # type: ignore[no-untyped-def]
                self.events.append({"message": message, **metadata})

            def save(self):  # type: ignore[no-untyped-def]
                FakeVault.saved = True
                return Path("identity.vault.json")

        posted: dict[str, object] = {}

        def fake_post_tweet(text, *, identity=None):  # type: ignore[no-untyped-def]
            posted["text"] = text
            posted["identity"] = identity
            return {"data": {"id": "12345"}}

        try:
            moltbook_claim_assistant.IdentityVault = FakeVault  # type: ignore[assignment]
            moltbook_claim_assistant.post_tweet = fake_post_tweet  # type: ignore[assignment]
            moltbook_claim_assistant.claim_status = lambda identity: {"ok": True, "status": "pending"}  # type: ignore[assignment]

            result = moltbook_claim_assistant.claim_with_x_oauth()
        finally:
            moltbook_claim_assistant.IdentityVault = old_identity_vault
            moltbook_claim_assistant.post_tweet = old_post_tweet
            moltbook_claim_assistant.claim_status = old_claim_status

        self.assertTrue(FakeVault.saved)
        self.assertEqual(result["tweet_id"], "12345")
        self.assertIn("MOLT-12345", posted["text"])

    def test_moltbook_refresh_claim_from_status_updates_vault(self) -> None:
        old_identity_vault = moltbook_claim_assistant.IdentityVault
        old_claim_status = moltbook_claim_assistant.claim_status

        class FakeVault:
            saved = False
            instance = None

            def __init__(self):
                self.data = empty_identity()
                self.data["moltbook"] = {"api_key": "mb_test"}
                self.events = []
                FakeVault.instance = self

            def load(self):  # type: ignore[no-untyped-def]
                return self

            def require_pin_ready(self):  # type: ignore[no-untyped-def]
                return None

            def event(self, message, **metadata):  # type: ignore[no-untyped-def]
                self.events.append({"message": message, **metadata})

            def save(self):  # type: ignore[no-untyped-def]
                FakeVault.saved = True
                return Path("identity.vault.json")

        try:
            moltbook_claim_assistant.IdentityVault = FakeVault  # type: ignore[assignment]
            moltbook_claim_assistant.claim_status = lambda identity: {
                "success": True,
                "status": "pending_claim",
                "agent": {"id": "agent-7", "name": "hellion-v2"},
                "claim_url": "https://www.moltbook.com/claim/new",
            }

            result = moltbook_claim_assistant.refresh_claim_from_status()
        finally:
            moltbook_claim_assistant.IdentityVault = old_identity_vault
            moltbook_claim_assistant.claim_status = old_claim_status

        self.assertTrue(FakeVault.saved)
        self.assertTrue(result["ok"])
        self.assertEqual(result["claim_url"], "https://www.moltbook.com/claim/new")
        self.assertEqual(FakeVault.instance.data["moltbook"]["agent_id"], "agent-7")

    def test_moltbook_inbox_claim_short_circuits_without_inbox(self) -> None:
        claims = extract_moltbook_claims({"messages": []})
        self.assertEqual(claims["claim_urls"], [])
        self.assertEqual(claims["codes"], [])

        identity = empty_identity()
        self.assertEqual(
            moltbook_claim_assistant.inbox_claim(identity),
            {"claim_urls": [], "urls": [], "codes": []},
        )

    def test_moltbook_inbox_claim_reports_agentmail_error_without_crashing(self) -> None:
        old_client = moltbook_claim_assistant.AgentMailClient

        class FailingAgentMailClient:
            def __init__(self, api_key: str):  # type: ignore[no-untyped-def]
                self.api_key = api_key

            def list_messages(self, inbox_id: str, limit: int = 20):  # type: ignore[no-untyped-def]
                raise OnboardingAPIError("agentmail", 403, "Forbidden")

        try:
            moltbook_claim_assistant.AgentMailClient = FailingAgentMailClient
            identity = empty_identity()
            identity["agentmail"] = {"api_key": "am_test", "inbox_id": "inbox-test"}
            claims = moltbook_claim_assistant.inbox_claim(identity)
        finally:
            moltbook_claim_assistant.AgentMailClient = old_client

        self.assertEqual(claims["claim_urls"], [])
        self.assertIn("agentmail API error 403", claims["error"])

    def test_moltbook_status_reports_missing_key_and_api_errors(self) -> None:
        self.assertEqual(
            moltbook_claim_assistant.claim_status(empty_identity()),
            {"ok": False, "reason": "missing Moltbook API key in identity vault"},
        )

        old_client = moltbook_claim_assistant.MoltbookClient

        class FailingMoltbookClient:
            def __init__(self, api_key: str):  # type: ignore[no-untyped-def]
                self.api_key = api_key

            def status(self):  # type: ignore[no-untyped-def]
                raise OnboardingAPIError("moltbook", 503, "downstream unavailable")

        try:
            moltbook_claim_assistant.MoltbookClient = FailingMoltbookClient
            identity = empty_identity()
            identity["moltbook"] = {"api_key": "mb_test"}
            status = moltbook_claim_assistant.claim_status(identity)
        finally:
            moltbook_claim_assistant.MoltbookClient = old_client

        self.assertEqual(status, {"ok": False, "reason": "moltbook API error 503: downstream unavailable"})

    def test_unwrap_preserves_non_dict_data_payloads(self) -> None:
        self.assertEqual(_unwrap("plain"), {"value": "plain"})
        self.assertEqual(
            _unwrap({"data": ["one", "two"], "meta": {"page": 1}}),
            {"value": ["one", "two"], "_raw": {"data": ["one", "two"], "meta": {"page": 1}}},
        )

    def test_optional_x_auth_email_failure_does_not_abort_authorization(self) -> None:
        old_env_config = x_oauth.env_config
        old_save_latest_auth_url = x_oauth.save_latest_auth_url
        old_save_oauth_session = x_oauth.save_oauth_session
        old_send_authorization_email = x_oauth.send_authorization_email
        old_open = x_oauth.webbrowser.open
        old_wait_for_callback = x_oauth.wait_for_callback
        old_exchange_code = x_oauth.exchange_code
        old_store_tokens = x_oauth.store_tokens

        try:
            x_oauth.env_config = lambda: {  # type: ignore[assignment]
                "client_id": "client-test",
                "client_secret": "secret-test",
                "redirect_uri": "http://127.0.0.1:8765/x/callback",
            }
            x_oauth.save_latest_auth_url = lambda url: Path("x_auth_url.txt")  # type: ignore[assignment]
            x_oauth.save_oauth_session = lambda **kwargs: Path("x_oauth_session.json")  # type: ignore[assignment]
            x_oauth.send_authorization_email = (  # type: ignore[assignment]
                lambda url, to_email="": (_ for _ in ()).throw(RuntimeError("smtp down"))
            )
            opened: list[str] = []
            x_oauth.webbrowser.open = lambda url: opened.append(url) or True  # type: ignore[assignment]
            x_oauth.wait_for_callback = lambda redirect_uri, expected_state="": {  # type: ignore[assignment]
                "code": "code-test",
                "state": expected_state,
            }
            x_oauth.exchange_code = lambda code, verifier, config: {"access_token": "token-test"}  # type: ignore[assignment]
            x_oauth.store_tokens = lambda tokens, config: {"stored": tokens["access_token"]}  # type: ignore[assignment]

            result = x_oauth.authorize_with_optional_email(email_url=True, to_email="owner@example.com")
        finally:
            x_oauth.env_config = old_env_config
            x_oauth.save_latest_auth_url = old_save_latest_auth_url
            x_oauth.save_oauth_session = old_save_oauth_session
            x_oauth.send_authorization_email = old_send_authorization_email
            x_oauth.webbrowser.open = old_open
            x_oauth.wait_for_callback = old_wait_for_callback
            x_oauth.exchange_code = old_exchange_code
            x_oauth.store_tokens = old_store_tokens

        self.assertEqual(result, {"stored": "token-test"})
        self.assertEqual(len(opened), 1)
        self.assertIn("client_id=client-test", opened[0])

    def test_x_auth_email_fallback_posts_to_agentmail_rest_api(self) -> None:
        old_identity_vault = x_oauth.IdentityVault
        old_agentmail_client = x_oauth.AgentMailClient
        old_requests_post = x_oauth.requests.post

        class FakeVault:
            def load(self):  # type: ignore[no-untyped-def]
                return type(
                    "LoadedVault",
                    (),
                    {
                        "data": {
                            "agentmail": {
                                "inbox_id": "hellion@agentmail.to",
                                "api_key": "am_test",
                            }
                        }
                    },
                )()

        class FakeAgentMailClient:
            def __init__(self, api_key: str):  # type: ignore[no-untyped-def]
                self.api_key = api_key
                self.base_url = "https://api.agentmail.to/v0"
                self._sdk_client = None

        sent: dict[str, object] = {}

        def fake_post(url, headers=None, json=None, timeout=None):  # type: ignore[no-untyped-def]
            sent.update({"url": url, "headers": headers, "json": json, "timeout": timeout})

            class Response:
                status_code = 202
                text = ""

            return Response()

        try:
            x_oauth.IdentityVault = FakeVault  # type: ignore[assignment]
            x_oauth.AgentMailClient = FakeAgentMailClient  # type: ignore[assignment]
            x_oauth.requests.post = fake_post  # type: ignore[assignment]

            x_oauth.send_authorization_email("https://x.example/auth", to_email="owner@example.com")
        finally:
            x_oauth.IdentityVault = old_identity_vault
            x_oauth.AgentMailClient = old_agentmail_client
            x_oauth.requests.post = old_requests_post

        self.assertEqual(sent["url"], "https://api.agentmail.to/v0/inboxes/hellion@agentmail.to/messages")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer am_test")
        self.assertEqual(sent["json"]["to"], "owner@example.com")
        self.assertIn("https://x.example/auth", sent["json"]["text"])

    def test_x_pkce_authorization_url_contains_required_scopes(self) -> None:
        verifier, challenge = pkce_pair()
        url = authorization_url(
            state="state-test",
            challenge=challenge,
            config={
                "client_id": "client-test",
                "client_secret": "secret-test",
                "redirect_uri": "http://127.0.0.1:8765/x/callback",
            },
        )

        self.assertTrue(verifier)
        self.assertIn("client_id=client-test", url)
        self.assertIn("tweet.write", url)
        self.assertIn("offline.access", url)
        self.assertIn("code_challenge=", url)

    def test_x_manual_callback_url_parser_extracts_code_and_state(self) -> None:
        parsed = parse_callback_url("http://127.0.0.1:8765/x/callback?state=s123&code=c456")

        self.assertEqual(parsed["state"], "s123")
        self.assertEqual(parsed["code"], "c456")

    def test_x_manual_callback_parser_accepts_fragment_params(self) -> None:
        parsed = parse_callback_url("http://127.0.0.1:8765/x/callback#state=s123&code=c456")

        self.assertEqual(parsed["state"], "s123")
        self.assertEqual(parsed["code"], "c456")

    def test_claw_bearer_token_is_sent_for_account_creation(self) -> None:
        class FakeSession:
            def __init__(self):
                self.headers = {}

            def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
                self.headers = kwargs["headers"]

                class Response:
                    status_code = 200
                    text = '{"apiKey":"mr_live_test"}'

                    def json(self):  # type: ignore[no-untyped-def]
                        return {"apiKey": "mr_live_test"}

                return Response()

        client = ClawRoyaleClient(onboarding_token="claw_test")
        fake = FakeSession()
        client.session = fake  # type: ignore[assignment]
        client.create_account(DEFAULT_PUBLIC_NAME, "0x" + "1" * 40)

        self.assertEqual(fake.headers["Authorization"], "Bearer claw_test")

    def test_claw_client_version_header_uses_single_source(self) -> None:
        class FakeSession:
            def __init__(self):
                self.headers = {}

            def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
                self.headers = kwargs["headers"]

                class Response:
                    status_code = 200
                    text = '{"erc8004Id":"1"}'

                    def json(self):  # type: ignore[no-untyped-def]
                        return {"erc8004Id": "1"}

                return Response()

        old = os.environ.get("CLAW_ROYALE_VERSION")
        try:
            os.environ["CLAW_ROYALE_VERSION"] = "7.7.7"
            client = ClawRoyaleClient(api_key="mr_test")
            fake = FakeSession()
            client.session = fake  # type: ignore[assignment]
            client.get_identity()
        finally:
            if old is None:
                os.environ.pop("CLAW_ROYALE_VERSION", None)
            else:
                os.environ["CLAW_ROYALE_VERSION"] = old

        self.assertEqual(fake.headers["X-Version"], "7.7.7")

    def test_claw_client_loadout_and_inventory_paths_match_docs(self) -> None:
        class FakeSession:
            def __init__(self):
                self.calls = []

            def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
                self.calls.append((method, url, kwargs))

                class Response:
                    status_code = 200
                    text = '{"success":true,"data":{}}'

                    def json(self):  # type: ignore[no-untyped-def]
                        return {"success": True, "data": {}}

                return Response()

        client = ClawRoyaleClient(api_key="mr_test")
        fake = FakeSession()
        client.session = fake  # type: ignore[assignment]

        client.me()
        client.join_status()
        client.waiting_games()
        client.preseason1_quests()
        client.preseason1_daily_quests()
        client.preseason1_summary()
        client.claim_preseason1_quest("attendance", 1)
        client.claim_preseason1_daily_quest("daily-kills")
        client.request_whitelist("0x" + "1" * 40)
        client.delete_identity()
        client.loadout()
        client.set_active_pack("pack-1", "idem-1")
        client.clear_active_pack("idem-2")
        client.set_relic_slot(1, "relic-1", "idem-3")
        client.clear_relic_slot(1, "idem-4")
        client.inventory_relics(limit=15)
        client.inventory_packs(limit=5)
        client.shop_listings()
        client.purchase_shop_listing("reforge_stone_bundle", 1, "idem-5")
        client.reforge_relic("relic-1", "effect_add", "idem-6")
        client.discard_relic("relic-1")
        client.discard_pack("pack-1")

        urls = [call[1] for call in fake.calls]
        self.assertIn("https://cdn.clawroyale.ai/api/accounts/me", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/join/status", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/games", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/preseason1/quests", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/preseason1/daily-quests", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/preseason1/me/summary", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/preseason1/quests/attendance/claim/1", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/preseason1/daily-quests/daily-kills/claim", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/whitelist/request", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/identity", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/loadout", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/loadout/pack", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/loadout/slot/1", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/inventory/relics", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/inventory/packs", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/shop/listings", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/shop/purchase", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/reforge", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/inventory/relics/relic-1", urls)
        self.assertIn("https://cdn.clawroyale.ai/api/inventory/packs/pack-1", urls)
        self.assertTrue(all("X-Version" in call[2]["headers"] for call in fake.calls))

    def test_loadout_shop_reforge_planner_prepares_paid_game_setup(self) -> None:
        plan = loadout_shop_reforge.build_prejoin_plan(
            loadout={
                "activePack": {"instanceId": "old-pack"},
                "subPack": {"instanceId": "sub-pack-1"},
                "slots": {},
            },
            packs={
                "packs": [
                    {"instanceId": "pack-g", "category": "goliath", "tier": "T3"},
                    {"instanceId": "pack-m", "category": "moltz_expert", "tier": "T2"},
                ]
            },
            relics={
                "relics": [
                    {"instanceId": "red-1", "typeIndex": 0, "tier": "T2", "affixes": [{"stat": "ATK", "value": 4}]},
                    {"instanceId": "green-1", "typeIndex": 1, "affixes": [{"stat": "MAX HP", "value": 8}]},
                    {"instanceId": "blue-1", "typeIndex": 2, "affixes": [{"stat": "ATK", "value": -2}]},
                ]
            },
            balance_smoltz=28000,
        )

        ops = plan["loadout"]["operations"]
        self.assertTrue(any(op.get("type") == "set_active_pack" and op.get("packInstanceId") == "pack-m" for op in ops))
        self.assertTrue(any(op.get("type") == "set_relic_slot" and op.get("slot") == "red" for op in ops))
        self.assertTrue(plan["loadout"]["complete_full_set"])
        self.assertEqual(plan["loadout"]["chosen"]["sub_pack"]["id"], "sub-pack-1")
        self.assertEqual(plan["reforge"][0]["relicInstanceId"], "blue-1")
        self.assertTrue(any(item["item"] == "reforge_stone_bundle" for item in plan["shop"]))
        self.assertEqual(plan["execution_order"], ["shop", "reforge", "loadout"])
        self.assertTrue(plan["ready_for_paid"])
        self.assertTrue(plan["needs_inventory_refresh"])

    def test_loadout_full_set_counts_already_equipped_components(self) -> None:
        plan = loadout_shop_reforge.build_prejoin_plan(
            loadout={
                "mainPack": {"instanceId": "main-equipped"},
                "subPack": {"instanceId": "sub-equipped"},
                "slots": {
                    "red": {"instanceId": "red-equipped"},
                    "green": {"instanceId": "green-equipped"},
                    "blue": {"instanceId": "blue-equipped"},
                },
            },
            packs={"packs": []},
            relics={"relics": []},
            balance_smoltz=500,
        )

        self.assertTrue(plan["loadout"]["complete_full_set"])
        self.assertEqual(plan["loadout"]["operations"], [])
        self.assertEqual(plan["loadout"]["chosen"]["main_pack"]["source"], "currently_equipped")
        self.assertEqual(plan["loadout"]["chosen"]["relics"]["red"]["id"], "red-equipped")

    def test_loadout_executor_is_dry_run_by_default(self) -> None:
        calls = []

        class FakeClient:
            def set_active_pack(self, *args):  # type: ignore[no-untyped-def]
                calls.append(args)
                return {"ok": True}

        result = loadout_shop_reforge.execute_loadout_operations(
            FakeClient(),
            [{"type": "set_active_pack", "packInstanceId": "pack-1"}],
        )

        self.assertTrue(result["results"][0]["dry_run"])
        self.assertEqual(calls, [])

    def test_shop_and_reforge_executors_are_dry_run_by_default(self) -> None:
        calls = []

        class FakeClient:
            def purchase_shop_listing(self, *args):  # type: ignore[no-untyped-def]
                calls.append(("purchase", args))
                return {"ok": True}

            def reforge_relic(self, *args):  # type: ignore[no-untyped-def]
                calls.append(("reforge", args))
                return {"ok": True}

        shop = loadout_shop_reforge.execute_shop_recommendations(
            FakeClient(),
            [{"type": "buy_shop_item", "item": "reforge_stone_bundle"}],
        )
        reforge = loadout_shop_reforge.execute_reforge_candidates(
            FakeClient(),
            [{"relicInstanceId": "relic-1", "recommendedItemKey": "effect_add"}],
        )

        self.assertTrue(shop["results"][0]["dry_run"])
        self.assertTrue(reforge["results"][0]["dry_run"])
        self.assertEqual(calls, [])

    def test_claw_runtime_prejoin_loadout_report_uses_client_and_applies_when_enabled(self) -> None:
        old_client = claw_runtime.ClawRoyaleClient
        old_apply = os.environ.get("CLAW_ROYALE_LOADOUT_AUTO_APPLY")
        old_shop = os.environ.get("CLAW_ROYALE_SHOP_AUTO_PURCHASE")
        old_reforge = os.environ.get("CLAW_ROYALE_REFORGE_AUTO_APPLY")
        calls = []

        class FakeClient:
            def __init__(self, api_key="", base_url=""):  # type: ignore[no-untyped-def]
                self.api_key = api_key
                self.base_url = base_url

            def loadout(self):  # type: ignore[no-untyped-def]
                return {"activePack": {"instanceId": "old-pack"}, "slots": {}}

            def inventory_relics(self, limit=15):  # type: ignore[no-untyped-def]
                return {"relics": [{"instanceId": "red-1", "typeIndex": 0, "affixes": [{"stat": "ATK", "value": 3}]}]}

            def inventory_packs(self, limit=5):  # type: ignore[no-untyped-def]
                return {"packs": [{"instanceId": "pack-1", "category": "moltz_expert"}]}

            def set_active_pack(self, pack_id, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("pack", pack_id, bool(idempotency_key)))
                return {"ok": True}

            def set_relic_slot(self, slot, relic_id, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("slot", slot, relic_id, bool(idempotency_key)))
                return {"ok": True}

            def purchase_shop_listing(self, listing_id, quantity, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("purchase", listing_id, quantity, bool(idempotency_key)))
                return {"ok": True}

            def reforge_relic(self, relic_id, item_key, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("reforge", relic_id, item_key, bool(idempotency_key)))
                return {"ok": True}

        try:
            os.environ["CLAW_ROYALE_LOADOUT_AUTO_APPLY"] = "true"
            os.environ.pop("CLAW_ROYALE_SHOP_AUTO_PURCHASE", None)
            os.environ.pop("CLAW_ROYALE_REFORGE_AUTO_APPLY", None)
            claw_runtime.ClawRoyaleClient = FakeClient  # type: ignore[assignment]
            report = claw_runtime.prejoin_loadout_report(
                claw_runtime.ClawRuntimeConfig(api_key="mr_test"),
                {"balance": 11000},
            )
        finally:
            claw_runtime.ClawRoyaleClient = old_client  # type: ignore[assignment]
            if old_apply is None:
                os.environ.pop("CLAW_ROYALE_LOADOUT_AUTO_APPLY", None)
            else:
                os.environ["CLAW_ROYALE_LOADOUT_AUTO_APPLY"] = old_apply
            if old_shop is None:
                os.environ.pop("CLAW_ROYALE_SHOP_AUTO_PURCHASE", None)
            else:
                os.environ["CLAW_ROYALE_SHOP_AUTO_PURCHASE"] = old_shop
            if old_reforge is None:
                os.environ.pop("CLAW_ROYALE_REFORGE_AUTO_APPLY", None)
            else:
                os.environ["CLAW_ROYALE_REFORGE_AUTO_APPLY"] = old_reforge

        self.assertTrue(report["ok"])
        self.assertTrue(report["auto_apply"])
        self.assertTrue(any(call[0] == "pack" for call in calls))
        self.assertFalse(any(call[0] in {"purchase", "reforge"} for call in calls))
        self.assertTrue(report["shop"]["results"][0]["dry_run"])
        self.assertTrue(report["reforge"]["results"][0]["dry_run"])

    def test_claw_runtime_refetches_inventory_after_auto_shop_or_reforge(self) -> None:
        old_client = claw_runtime.ClawRoyaleClient
        old_apply = os.environ.get("CLAW_ROYALE_LOADOUT_AUTO_APPLY")
        old_shop = os.environ.get("CLAW_ROYALE_SHOP_AUTO_PURCHASE")
        old_reforge = os.environ.get("CLAW_ROYALE_REFORGE_AUTO_APPLY")
        calls = []

        class FakeClient:
            def __init__(self, api_key="", base_url=""):  # type: ignore[no-untyped-def]
                self.relic_calls = 0
                self.pack_calls = 0

            def loadout(self):  # type: ignore[no-untyped-def]
                calls.append(("loadout",))
                return {"activePack": {}, "slots": {}}

            def inventory_relics(self, limit=15):  # type: ignore[no-untyped-def]
                self.relic_calls += 1
                calls.append(("relics", self.relic_calls))
                if self.relic_calls == 1:
                    return {"relics": [{"instanceId": "bad-1", "typeIndex": 0, "affixes": [{"stat": "ATK", "value": -3}]}]}
                return {"relics": [{"instanceId": "good-1", "typeIndex": 0, "affixes": [{"stat": "ATK", "value": 7}]}]}

            def inventory_packs(self, limit=5):  # type: ignore[no-untyped-def]
                self.pack_calls += 1
                calls.append(("packs", self.pack_calls))
                return {"packs": [{"instanceId": "pack-1", "category": "moltz_expert"}]}

            def purchase_shop_listing(self, listing_id, quantity, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("purchase", listing_id))
                return {"ok": True}

            def reforge_relic(self, relic_id, item_key, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("reforge", relic_id, item_key))
                return {"ok": True}

            def set_active_pack(self, pack_id, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("pack", pack_id))
                return {"ok": True}

            def set_relic_slot(self, slot, relic_id, idempotency_key):  # type: ignore[no-untyped-def]
                calls.append(("slot", relic_id))
                return {"ok": True}

        try:
            os.environ["CLAW_ROYALE_LOADOUT_AUTO_APPLY"] = "true"
            os.environ["CLAW_ROYALE_SHOP_AUTO_PURCHASE"] = "true"
            os.environ["CLAW_ROYALE_REFORGE_AUTO_APPLY"] = "true"
            claw_runtime.ClawRoyaleClient = FakeClient  # type: ignore[assignment]
            report = claw_runtime.prejoin_loadout_report(
                claw_runtime.ClawRuntimeConfig(api_key="mr_test"),
                {"balance": 28000},
            )
        finally:
            claw_runtime.ClawRoyaleClient = old_client  # type: ignore[assignment]
            if old_apply is None:
                os.environ.pop("CLAW_ROYALE_LOADOUT_AUTO_APPLY", None)
            else:
                os.environ["CLAW_ROYALE_LOADOUT_AUTO_APPLY"] = old_apply
            if old_shop is None:
                os.environ.pop("CLAW_ROYALE_SHOP_AUTO_PURCHASE", None)
            else:
                os.environ["CLAW_ROYALE_SHOP_AUTO_PURCHASE"] = old_shop
            if old_reforge is None:
                os.environ.pop("CLAW_ROYALE_REFORGE_AUTO_APPLY", None)
            else:
                os.environ["CLAW_ROYALE_REFORGE_AUTO_APPLY"] = old_reforge

        self.assertTrue(report["ok"])
        self.assertGreaterEqual(len([call for call in calls if call[0] == "relics"]), 2)
        self.assertTrue(any(call == ("slot", "good-1") for call in calls))

    def test_claw_siwe_message_matches_frontend_shape(self) -> None:
        message = build_claw_siwe_message(
            address="0x" + "1" * 40,
            domain="www.clawroyale.ai",
            uri="https://www.clawroyale.ai",
            chain_id=612055,
            nonce="abc",
            issued_at="2026-06-09T17:30:00.000Z",
        )

        self.assertIn("www.clawroyale.ai wants you to sign in with your Ethereum account:", message)
        self.assertIn("Sign in with Ethereum to ClawRoyale.", message)
        self.assertIn("Chain ID: 612055", message)
        self.assertIn("Nonce: abc", message)

    def test_claw_identity_token_extracts_nested_agent_id(self) -> None:
        payload = {"data": {"identity": {"erc8004Id": 12345}}}

        self.assertEqual(claw_identity_token.extract_identity_id(payload), "12345")

    def test_claw_identity_token_extracts_minted_transfer_token_id(self) -> None:
        owner = "0x" + "a" * 40
        receipt = {
            "logs": [
                {
                    "address": claw_identity_token.IDENTITY_REGISTRY_ADDRESS,
                    "topics": [
                        claw_identity_token.TRANSFER_TOPIC,
                        "0x" + "0" * 64,
                        "0x" + "0" * 24 + "a" * 40,
                        "0x" + hex(4321)[2:].rjust(64, "0"),
                    ],
                }
            ]
        }

        self.assertEqual(claw_identity_token.extract_minted_token_id(receipt, owner), "4321")

    def test_claw_identity_attach_stores_token_status(self) -> None:
        class FakeVault:
            def __init__(self):
                self.data = empty_identity()
                self.data["claw_royale"] = {"api_key": "mr_test"}
                self.saved = False
                self.events = []

            def load(self):  # type: ignore[no-untyped-def]
                return self

            def require_pin_ready(self):  # type: ignore[no-untyped-def]
                return None

            def event(self, message, **metadata):  # type: ignore[no-untyped-def]
                self.events.append({"message": message, **metadata})

            def save(self):  # type: ignore[no-untyped-def]
                self.saved = True
                return Path("identity.vault.json")

        class FakeClient:
            def post_identity(self, token_id):  # type: ignore[no-untyped-def]
                return {"erc8004Id": token_id, "ok": True}

        old_load_client = claw_identity_token.load_client
        try:
            claw_identity_token.load_client = lambda identity: FakeClient()  # type: ignore[assignment]
            vault = FakeVault()
            result = claw_identity_token.attach_identity_token(98765, vault=vault)
        finally:
            claw_identity_token.load_client = old_load_client

        self.assertTrue(vault.saved)
        self.assertTrue(result["identity_ready"])
        self.assertEqual(vault.data["claw_royale"]["erc8004_id"], "98765")

    def test_claw_runtime_builds_websocket_base_from_api_base(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(
            api_key="mr_test",
            api_base="https://cdn.clawroyale.ai/api",
            version="1.9.0",
            enabled=True,
        )

        self.assertEqual(config.ws_base_url, "wss://cdn.clawroyale.ai")
        self.assertEqual(config.headers["X-API-Key"], "mr_test")
        self.assertEqual(config.headers["X-Version"], "1.9.0")

    def test_claw_runtime_defaults_enabled_unless_explicitly_disabled(self) -> None:
        old_enabled = os.environ.get("CLAW_ROYALE_RUNTIME_ENABLED")
        old_key = os.environ.get("CLAW_ROYALE_API_KEY")
        old_version = os.environ.get("CLAW_ROYALE_VERSION")
        old_discover = claw_runtime.discover_version
        try:
            os.environ.pop("CLAW_ROYALE_RUNTIME_ENABLED", None)
            os.environ["CLAW_ROYALE_API_KEY"] = "mr_test"
            os.environ["CLAW_ROYALE_VERSION"] = "1.9.0"
            claw_runtime.discover_version = lambda api_base=claw_runtime.CLAW_API_BASE: "1.9.0"  # type: ignore[assignment]
            self.assertTrue(claw_runtime.load_config().enabled)
            os.environ["CLAW_ROYALE_RUNTIME_ENABLED"] = "false"
            self.assertFalse(claw_runtime.load_config().enabled)
        finally:
            claw_runtime.discover_version = old_discover  # type: ignore[assignment]
            for key, value in (
                ("CLAW_ROYALE_RUNTIME_ENABLED", old_enabled),
                ("CLAW_ROYALE_API_KEY", old_key),
                ("CLAW_ROYALE_VERSION", old_version),
            ):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_claw_runtime_normalizes_paid_mode_to_offchain(self) -> None:
        self.assertEqual(claw_runtime.normalize_game_mode("paid"), "offchain")
        self.assertEqual(claw_runtime.normalize_game_mode("paid_offchain"), "offchain")
        self.assertEqual(claw_runtime.normalize_game_mode("onchain"), "onchain")
        self.assertEqual(claw_runtime.normalize_game_mode("free"), "free")
        self.assertEqual(claw_runtime.normalize_game_mode("unknown"), "offchain")

    def test_claw_runtime_has_rotating_websocket_path_candidates(self) -> None:
        old_paths = os.environ.get("CLAW_ROYALE_WS_PATHS")
        old_path = os.environ.get("CLAW_ROYALE_WS_PATH")
        try:
            os.environ.pop("CLAW_ROYALE_WS_PATHS", None)
            os.environ.pop("CLAW_ROYALE_WS_PATH", None)
            defaults = claw_runtime.websocket_paths()
            self.assertEqual(defaults, ["/ws/join", "/ws/agent"])

            os.environ["CLAW_ROYALE_WS_PATHS"] = "agent-live,/custom,wss://example.test/ws"
            self.assertEqual(claw_runtime.websocket_paths(), ["/agent-live", "/custom", "wss://example.test/ws"])
        finally:
            for key, value in (("CLAW_ROYALE_WS_PATHS", old_paths), ("CLAW_ROYALE_WS_PATH", old_path)):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_claw_runtime_accepts_full_websocket_urls(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", api_base="https://cdn.clawroyale.ai/api")

        self.assertEqual(
            claw_runtime.websocket_url(config, "wss://example.test/live"),
            "wss://example.test/live",
        )
        self.assertEqual(
            claw_runtime.websocket_url(config, "/ws/agent"),
            "wss://cdn.clawroyale.ai/ws/agent",
        )

    def test_claw_runtime_uses_fast_probe_delay_for_404_routes(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", max_reconnect_seconds=90)

        self.assertEqual(
            claw_runtime.reconnect_delay_seconds(config, 12, RuntimeError("server rejected WebSocket connection: HTTP 404")),
            15,
        )
        self.assertEqual(claw_runtime.reconnect_delay_seconds(config, 2, RuntimeError("timeout")), 10)

    def test_claw_runtime_route_probe_delay_respects_env_override(self) -> None:
        old_probe = os.environ.get("CLAW_ROYALE_ROUTE_PROBE_SECONDS")
        try:
            os.environ["CLAW_ROYALE_ROUTE_PROBE_SECONDS"] = "7"
            config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", max_reconnect_seconds=90)
            delay = claw_runtime.reconnect_delay_seconds(config, 20, RuntimeError("HTTP 404"))
        finally:
            if old_probe is None:
                os.environ.pop("CLAW_ROYALE_ROUTE_PROBE_SECONDS", None)
            else:
                os.environ["CLAW_ROYALE_ROUTE_PROBE_SECONDS"] = old_probe

        self.assertEqual(delay, 7)

    def test_claw_runtime_discover_version_falls_back_after_update_conflict(self) -> None:
        old_reconcile = claw_runtime.reconcile_claw_version
        old_status = claw_runtime.update_status
        old_env = os.environ.get("CLAW_ROYALE_VERSION")
        updates = []
        try:
            os.environ["CLAW_ROYALE_VERSION"] = "9.9.9"
            claw_runtime.reconcile_claw_version = lambda api_base: (_ for _ in ()).throw(RuntimeError("schema mismatch"))  # type: ignore[assignment]
            claw_runtime.update_status = lambda **kwargs: updates.append(kwargs)  # type: ignore[assignment]
            version = claw_runtime.discover_version("https://cdn.clawroyale.ai/api")
        finally:
            claw_runtime.reconcile_claw_version = old_reconcile  # type: ignore[assignment]
            claw_runtime.update_status = old_status  # type: ignore[assignment]
            if old_env is None:
                os.environ.pop("CLAW_ROYALE_VERSION", None)
            else:
                os.environ["CLAW_ROYALE_VERSION"] = old_env

        self.assertEqual(version, "9.9.9")
        self.assertTrue(any("version discovery failed" in item.get("last_error", "") for item in updates))

    def test_claw_runtime_account_status_summary_handles_dependency_failure(self) -> None:
        old_client = claw_runtime.ClawRoyaleClient
        try:
            class FailingClient:
                def __init__(self, api_key, base_url):  # type: ignore[no-untyped-def]
                    self.api_key = api_key
                    self.base_url = base_url

                def me(self):  # type: ignore[no-untyped-def]
                    raise RuntimeError("dependency mismatch: unexpected response shape")

            claw_runtime.ClawRoyaleClient = FailingClient  # type: ignore[assignment]
            summary = claw_runtime.account_status_summary(
                claw_runtime.ClawRuntimeConfig(api_key="mr_test", api_base="https://cdn.clawroyale.ai/api")
            )
        finally:
            claw_runtime.ClawRoyaleClient = old_client  # type: ignore[assignment]

        self.assertFalse(summary["ok"])
        self.assertIn("dependency mismatch", summary["error"])

    def test_claw_runtime_extracts_nested_game_id(self) -> None:
        payload = {"type": "agent_view", "data": {"view": {"currentGame": {"id": "game-123"}}}}

        self.assertEqual(claw_runtime.extract_game_id(payload), "game-123")
        self.assertEqual(claw_runtime.extract_game_id(claw_runtime.unwrap_snapshot(payload)), "game-123")

    def test_claw_runtime_unwraps_payload_state_aliases(self) -> None:
        payload = {"type": "agent_view", "payload": {"gameId": "game-payload", "canAct": True}}

        self.assertEqual(claw_runtime.unwrap_snapshot(payload), {"gameId": "game-payload", "canAct": True})
        self.assertEqual(claw_runtime.extract_game_id(payload), "game-payload")

    def test_claw_runtime_does_not_treat_cooldown_frames_as_snapshots(self) -> None:
        self.assertIsNone(claw_runtime.unwrap_snapshot({"type": "can_act_changed", "canAct": True}))
        self.assertIsNone(claw_runtime.unwrap_snapshot({"type": "action_result", "canAct": False, "cooldownRemainingMs": 30000}))

    def test_claw_runtime_action_envelope_matches_contract(self) -> None:
        envelope = claw_runtime.action_envelope(
            {"type": "move", "regionId": "r2", "reason": "death-zone pressure", "_warnings": []}
        )

        self.assertEqual(envelope["type"], "action")
        self.assertEqual(envelope["data"], {"type": "move", "regionId": "r2", "reason": "death-zone pressure"})
        self.assertIn("Hellion", envelope["thought"])
        self.assertNotIn("_warnings", envelope["data"])

    def test_public_action_thought_taunts_without_private_reason_dump(self) -> None:
        thought = claw_runtime.public_action_thought(
            {"type": "equip", "itemId": "dagger-1", "reason": "free equip upgrade from fist to dagger"}
        )

        self.assertIn("Hellion", thought)
        self.assertNotIn("free equip upgrade", thought)

    def test_claw_runtime_snapshot_summary_exposes_live_dashboard_stats(self) -> None:
        summary = claw_runtime.snapshot_summary(
            {
                "gameId": "game-1",
                "turn": 7,
                "canAct": True,
                "view": {
                    "self": {"id": "me", "hp": 52, "maxHp": 100, "ep": 3, "maxEp": 8, "atk": 19},
                    "currentRegion": {"id": "r1", "name": "Ruin Gate", "terrain": "Ruin"},
                    "visibleAgents": [{"id": "enemy-1"}],
                    "visibleMonsters": [{"id": "mob-1"}],
                    "visibleItems": [{"id": "loot-1"}],
                    "alertGauge": 4,
                },
            }
        )

        self.assertEqual(summary["hp"], 52)
        self.assertEqual(summary["ep"], 3)
        self.assertEqual(summary["region_name"], "Ruin Gate")
        self.assertEqual(summary["visible_agents"], 1)
        self.assertEqual(summary["visible_monsters"], 1)

    def test_live_game_map_models_hex_contents_and_route_hints(self) -> None:
        payload = game_map.build_live_map(
            {
                "gameId": "game-1",
                "turn": 7,
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 4, "atk": 20},
                    "currentRegion": {
                        "id": "r1",
                        "name": "Center",
                        "terrain": "Plain",
                        "weather": "Fog",
                        "items": [
                            {"id": "cash-1", "typeId": "smoltz_bundle"},
                            {"id": "dagger-1", "typeId": "dagger"},
                            {"id": "relic-1", "typeId": "relic_red"},
                            {"id": "cache-1", "typeId": "supply_cache"},
                        ],
                        "interactables": [{"id": "med-1", "type": "medical facility"}, {"id": "tower-1", "type": "broadcast tower"}],
                        "connections": [{"id": "ruin-1", "terrain": "Ruin"}],
                    },
                    "visibleRegions": [{"id": "ruin-1", "name": "Old Vault", "terrain": "Ruin"}],
                    "visibleAgents": [{"id": "rival-1", "name": "Rival", "hp": 30, "atk": 8}],
                    "visibleMonsters": [{"id": "guardian-1", "name": "Guardian", "hp": 40, "atk": 12}],
                },
            }
        )

        current = next(item for item in payload["hexes"] if item["is_current"])
        self.assertTrue(payload["ok"])
        self.assertIn("H", current["contents"])
        self.assertIn("W", current["contents"])
        self.assertIn("$", current["contents"])
        self.assertIn("R", current["contents"])
        self.assertIn("S", current["contents"])
        self.assertIn("+", current["contents"])
        self.assertIn("B", current["contents"])
        self.assertIn("G", current["contents"])
        self.assertIn("$", payload["legend"])
        self.assertIn("R", payload["legend"])
        self.assertIn("B", payload["legend"])
        self.assertEqual(current["badges"][0]["symbol"], "H")
        self.assertTrue(any(badge["symbol"] == "R" for badge in current["badges"]))
        self.assertEqual(payload["summary"]["items"], 4)
        self.assertEqual(payload["summary"]["relics"], 1)
        self.assertEqual(payload["summary"]["medical"], 1)
        self.assertEqual(payload["summary"]["broadcast"], 1)
        self.assertEqual(payload["summary"]["guardians"], 1)
        self.assertTrue(any(hint["type"] == "loot" for hint in payload["routes"]))

    def test_live_game_map_treats_visible_agent_guardians_as_guardians(self) -> None:
        payload = game_map.build_live_map(
            {
                "gameId": "game-1",
                "view": {
                    "self": {"id": "me", "hp": 90, "ep": 4},
                    "currentRegion": {"id": "r1", "name": "Lab", "terrain": "Cave"},
                    "visibleAgents": [{"id": "guardian-1", "name": "Guardian", "hp": 150, "atk": 10}],
                },
            }
        )

        current = next(item for item in payload["hexes"] if item["is_current"])
        self.assertEqual([agent["kind"] for agent in current["agents"]], ["self"])
        self.assertEqual(current["monsters"][0]["id"], "guardian-1")
        self.assertIn("G", current["contents"])
        self.assertNotIn("A", current["contents"])

    def test_render_stats_includes_public_wallets(self) -> None:
        old_values = {key: os.environ.get(key) for key in ("CERBERUS_AGENT_EOA_ADDRESS", "CERBERUS_OWNER_EOA_ADDRESS", "CERBERUS_MOLTY_WALLET_ADDRESS")}
        try:
            os.environ["CERBERUS_AGENT_EOA_ADDRESS"] = "0x" + "1" * 40
            os.environ["CERBERUS_OWNER_EOA_ADDRESS"] = "0x" + "2" * 40
            os.environ["CERBERUS_MOLTY_WALLET_ADDRESS"] = "0x" + "3" * 40
            payload = render_app.stats()
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

        self.assertEqual(payload["public_wallets"]["owner_eoa"], "0x" + "2" * 40)

    def test_render_stats_exposes_social_queue_count(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                social_runtime.enqueue_social_effects(
                    [{"type": "moltybook_draft", "category": "test", "content": "queued"}]
                )
                payload = render_app.stats()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(payload["autonomy"]["social_queue"], 1)
        self.assertEqual(payload["social_queue"][0]["type"], "moltybook_draft")

    def test_dashboard_is_vector_map_command_center(self) -> None:
        html = render_app.dashboard_html().decode("utf-8")

        self.assertIn('id="game-map"', html)
        self.assertIn('id="map-legend"', html)
        self.assertIn("renderLegend", html)
        self.assertIn("badgeFill", html)
        self.assertIn('id="owner-form"', html)
        self.assertIn('id="paid-ready"', html)
        self.assertIn('id="healthz"', html)
        self.assertIn('id="current-intent"', html)
        self.assertIn('id="action-audit"', html)
        self.assertIn('id="stuck-doctor"', html)
        self.assertIn('id="stale-paid-rooms"', html)
        self.assertIn('id="social-queue"', html)
        self.assertIn('id="launch-report"', html)
        self.assertIn('id="deployment"', html)
        self.assertIn("launch.blockers", html)
        self.assertIn('fetchJson("/admin/social-drain"', html)
        self.assertIn('fetchJson("/admin/launch-report"', html)
        self.assertIn("<line x1=", html)
        self.assertIn("map.summary", html)
        self.assertIn("Live tactical map", html)
        self.assertNotIn("<iframe id=\"feed\"", html)

    def test_owner_command_understands_map_paid_and_leave_context(self) -> None:
        categories = owner_command_cortex.command_categories(
            "force refresh the live hex map, diagnose paid ready blockers, and abort stale game"
        )

        self.assertIn("map", categories)
        self.assertIn("paid_mode", categories)
        self.assertIn("leave_game", categories)
        self.assertIn("diagnostic", categories)

    def test_owner_command_understands_broad_deterministic_command_families(self) -> None:
        text = (
            "stop idling, equip strongest weapon, collect moltz, heal if low hp, review memory lessons, "
            "diagnose render disk, top up loadout, post to moltbook, check wallet balance, and join paid games"
        )
        categories = owner_command_cortex.command_categories(text)

        for category in ("weapon", "value", "heal", "memory", "deploy", "loadout", "social", "wallet", "paid_mode"):
            self.assertIn(category, categories)

    def test_claw_runtime_action_audit_is_bounded_and_records_intent(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                for index in range(30):
                    rows = claw_runtime.append_action_audit(
                        {
                            "kind": "action_sent",
                            "action": {"type": "move", "regionId": f"r{index}"},
                            "reason": "test route",
                        }
                    )
                status = runtime_state.read_json(runtime_state.claw_runtime_status_file())
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(len(rows), 25)
        self.assertEqual(status["action_audit"][-1]["action"]["regionId"], "r29")
        self.assertIn("move", claw_runtime.runtime_intent({"type": "move", "regionId": "r1", "reason": "scout"}))

    def test_profit_simulator_reports_policy_gaps_and_extended_scenarios(self) -> None:
        report = profit_simulator.simulate(games_per_day=61, target_per_day=1000)

        self.assertIn("gap_smoltz_per_day", report)
        self.assertIn("policy_gaps", report)
        self.assertTrue(any(row["scenario"] == "deathzone_escape_with_loot" for row in report["scenarios"]))

    def test_launch_doctor_report_includes_map_and_profit_sections(self) -> None:
        report = launch_doctor.launch_report()

        self.assertIn("runtime", report)
        self.assertIn("profit", report)
        self.assertIn("live_map_ok", report["runtime"])
        self.assertIn("env_lint", report)
        self.assertIn("social_queue", report["runtime"])
        self.assertIn("stale_paid_rooms", report["runtime"])

    def test_claw_runtime_hello_frame_follows_unified_join_docs(self) -> None:
        paid = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        free = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="free")

        self.assertEqual(
            claw_runtime.hello_frame(
                paid,
                {
                    "decision": "ASK_ENTRY_TYPE",
                    "availableGames": [
                        {
                            "entryType": "paid",
                            "gameId": "paid-ready-1",
                            "playerCount": 1,
                            "playersNeededToStart": 1,
                        }
                    ],
                },
            ),
            {"type": "hello", "entryType": "paid", "mode": "offchain"},
        )
        self.assertEqual(claw_runtime.hello_frame(free, {"decision": "FREE_ONLY"}), {"type": "hello", "entryType": "free"})
        self.assertIsNone(claw_runtime.hello_frame(paid, {"decision": "ALREADY_IN_GAME"}))

    def test_claw_runtime_prefers_occupied_free_room_over_empty_paid_rooms(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "rooms": [
                {"entryType": "paid", "players": []},
                {"entryType": "premium", "agentCount": 0},
                {"entryType": "free", "players": [{"id": "viewer-agent"}]},
            ],
        }

        old_fallback = os.environ.get("CLAW_ROYALE_FREE_FALLBACK_ENABLED")
        try:
            os.environ.pop("CLAW_ROYALE_FREE_FALLBACK_ENABLED", None)
            self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})
            os.environ["CLAW_ROYALE_FREE_FALLBACK_ENABLED"] = "true"
            self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})
        finally:
            if old_fallback is None:
                os.environ.pop("CLAW_ROYALE_FREE_FALLBACK_ENABLED", None)
            else:
                os.environ["CLAW_ROYALE_FREE_FALLBACK_ENABLED"] = old_fallback
        self.assertEqual(
            claw_runtime.room_choice_summary(welcome),
            {
                "paid_rooms": 2,
                "paid_occupied": 0,
                "paid_total_occupants": 0,
                "paid_competitors": 0,
                "free_rooms": 1,
                "free_occupied": 1,
                "free_total_occupants": 1,
                "free_competitors": 1,
                "unknown_rooms": 0,
                "stale_paid_rooms": 0,
            },
        )

    def test_claw_runtime_does_not_count_guardians_as_paid_competitors(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {
                    "entryType": "paid",
                    "agents": [{"id": f"guardian-{index}", "name": "Guardian"} for index in range(9)],
                },
                {"entryType": "free", "players": [{"id": "actual-agent", "name": "Rival"}]},
            ],
        }
        summary = claw_runtime.room_choice_summary(welcome)

        self.assertEqual(summary["paid_total_occupants"], 9)
        self.assertEqual(summary["paid_competitors"], 0)
        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})

    def test_claw_runtime_joins_paid_only_at_last_start_slot(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "paid-ready-1",
                    "players": [{"id": "rival-1", "name": "Big League Rival"}],
                    "playersNeededToStart": 1,
                },
            ],
        }

        self.assertTrue(claw_runtime.paid_room_is_competitive(welcome))
        self.assertTrue(claw_runtime.paid_room_is_last_slot_ready(welcome))
        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "paid", "mode": "offchain"})

    def test_claw_runtime_falls_back_when_paid_start_distance_is_unknown(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {"entryType": "paid", "gameId": "paid-unknown-1", "playerCount": 8, "maxPlayers": 30},
                {"entryType": "free", "gameId": "free-1", "playerCount": 1},
            ],
        }

        self.assertTrue(claw_runtime.paid_room_is_competitive(welcome))
        self.assertFalse(claw_runtime.paid_room_is_last_slot_ready(welcome))
        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})

    def test_claw_runtime_falls_back_when_paid_selection_is_ambiguous(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "paid-ready-1",
                    "playerCount": 9,
                    "requiredPlayers": 10,
                },
                {
                    "entryType": "paid",
                    "gameId": "paid-other-1",
                    "playerCount": 2,
                    "requiredPlayers": 10,
                },
                {"entryType": "free", "gameId": "free-1", "playerCount": 1},
            ],
        }

        self.assertFalse(claw_runtime.paid_room_is_last_slot_ready(welcome))
        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})

    def test_claw_runtime_prefers_fresh_waiting_game_probe_for_join_gate(self) -> None:
        welcome = {
            "type": "welcome",
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {"paidRoom": {"ok": True}},
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "stale-welcome-room",
                    "playerCount": 9,
                    "requiredPlayers": 10,
                }
            ],
        }
        probe = {
            "ok": True,
            "games": [
                {
                    "entryType": "paid",
                    "gameId": "fresh-probe-room",
                    "playerCount": 3,
                    "requiredPlayers": 10,
                },
                {"entryType": "free", "gameId": "free-1", "playerCount": 1},
            ],
        }

        context = claw_runtime.join_selection_context(welcome, probe)

        self.assertEqual(
            [room.get("gameId") for room in claw_runtime.room_entries(context)],
            ["fresh-probe-room", "free-1"],
        )
        self.assertFalse(claw_runtime.paid_room_is_last_slot_ready(context))

    def test_claw_runtime_probe_failure_never_reuses_welcome_paid_room(self) -> None:
        welcome = {
            "type": "welcome",
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "possibly-stale-welcome-room",
                    "playerCount": 9,
                    "requiredPlayers": 10,
                }
            ],
        }

        context = claw_runtime.join_selection_context(welcome, {"ok": False, "error": "timeout"})

        self.assertFalse(context["paidRoomProbeVerified"])
        self.assertEqual(claw_runtime.room_entries(context), [])
        self.assertFalse(claw_runtime.paid_room_is_last_slot_ready(context))

    def test_claw_runtime_falls_back_when_paid_room_metadata_is_absent(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {"paidRoom": {"ok": True, "mode": {"offchain": True}}},
            "rooms": [{"entryType": "free", "playerCount": 2}],
        }

        self.assertFalse(claw_runtime.paid_room_is_competitive(welcome))
        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})

    def test_claw_runtime_ignores_known_stale_paid_rooms_when_joining(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.remember_stale_paid_room("stale-paid-1", reason="waiting too long")
                config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
                welcome = {
                    "decision": "ASK_ENTRY_TYPE",
                    "availableGames": [
                        {
                            "entryType": "paid",
                            "gameId": "stale-paid-1",
                            "playerCount": 4,
                            "playersNeededToStart": 1,
                        },
                        {"entryType": "free", "gameId": "free-1", "playerCount": 1},
                    ],
                }
                summary = claw_runtime.room_choice_summary(welcome)
                frame = claw_runtime.hello_frame(config, welcome)
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(summary["stale_paid_rooms"], 1)
        self.assertEqual(summary["paid_rooms"], 0)
        self.assertEqual(frame, {"type": "hello", "entryType": "free"})

    def test_claw_runtime_records_paid_waiting_games_as_stale_candidates(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                rows = claw_runtime.record_stale_paid_waiting_games(
                    {
                        "currentGames": [
                            {"entryType": "paid", "gameStatus": "waiting", "gameId": "paid-wait-1"},
                            {"entryType": "free", "gameStatus": "waiting", "gameId": "free-wait-1"},
                        ]
                    }
                )
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(rows[-1]["room_id"], "paid-wait-1")

    def test_lesson_compiler_persists_repeated_reject_lessons(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.append_match_evidence(
                    {
                        "state": {
                            "terrain": "ruins",
                            "death_zone": False,
                            "visible_agents": 0,
                            "visible_monsters": 1,
                            "visible_items": 0,
                        },
                        "action": {"type": "attack", "targetId": "guardian-1"},
                        "outcome": {"ok": False, "message": "TARGET_BLOCKED"},
                    }
                )
                runtime_state.append_match_evidence(
                    {
                        "state": {
                            "terrain": "ruins",
                            "death_zone": False,
                            "visible_agents": 0,
                            "visible_monsters": 1,
                            "visible_items": 0,
                        },
                        "action": {"type": "attack", "targetId": "guardian-1"},
                        "outcome": {"ok": False, "message": "TARGET_BLOCKED"},
                    }
                )
                memory = CompactMemoryStore(
                    path=Path(tmp) / "compact.json",
                    encrypted_path=Path(tmp) / "compact.vault.json",
                ).load()
                longterm = LongTermMemoryStore(Path(tmp) / "longterm.sqlite")

                report = lesson_compiler.compile_lessons(memory=memory, longterm=longterm, min_count=2)
                lessons = CompactMemoryStore(
                    path=Path(tmp) / "compact.json",
                    encrypted_path=Path(tmp) / "compact.vault.json",
                ).load().data.get("lessons", [])
                rows = longterm.top(kind="lesson", scope="claw_royale", limit=5)
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertGreater(report["lesson_count"], 0)
        self.assertTrue(any("blocked_reject" in lesson for lesson in lessons))
        self.assertTrue(any("blocked_reject" in row["text"] for row in rows))

    def test_render_launch_blockers_surface_waiting_paid_games(self) -> None:
        blockers = render_app.runtimeBlockers_server(
            {
                "ok": True,
                "env": {
                    "CERBERUS_PIN": True,
                    "CLAW_ROYALE_API_KEY": True,
                    "CLAW_ROYALE_ERC8004_ID": True,
                    "CERBERUS_AGENT_EOA_PRIVATE_KEY": True,
                    "CLAW_ROYALE_RUNTIME_ENABLED": True,
                },
                "memory_writable": True,
            },
            {
                "state": "waiting_for_running_game",
                "account": {
                    "currentGames": [
                        {"entryType": "paid", "gameStatus": "waiting", "gameId": "stale-paid-1"},
                    ]
                },
            },
        )

        self.assertIn("paid game waiting for opponents: stale-paid-1", blockers)

    def test_render_deployment_info_exposes_disk_and_version_status(self) -> None:
        info = render_app.deployment_info(
            {
                "memory_dir": "/var/data/.cerberus",
                "memory_writable": True,
                "longterm_memory": {"path": "/var/data/.cerberus/hellion.longterm.sqlite", "items": 12, "bytes": 4096},
            },
            {"configured_version": "1.9.0", "live_version": "1.9.3"},
        )

        self.assertTrue(info["memory_writable"])
        self.assertEqual(info["longterm_items"], 12)
        self.assertEqual(info["live_claw_version"], "1.9.3")

    def test_stuck_state_doctor_recommends_support_for_paid_waiting_game(self) -> None:
        blockers = ["paid game waiting for opponents: stale-paid-1"]
        doctor = render_app.stuck_state_doctor(
            {"ok": True, "memory_writable": True},
            {"state": "waiting_for_running_game", "last_error": "joined but waiting for running game frame"},
            blockers,
            {"target_met": True},
        )

        self.assertEqual(doctor["summary"], blockers[0])
        self.assertTrue(any("Claw support" in item for item in doctor["recommendations"]))

    def test_claw_runtime_keeps_paid_when_paid_room_is_occupied(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "availableGames": [
                {
                    "entryType": "paid",
                    "gameId": "paid-ready-1",
                    "playerCount": 1,
                    "playersNeededToStart": 1,
                },
                {"entryType": "free", "playerCount": 3},
            ],
        }

        self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "paid", "mode": "offchain"})

    def test_claw_runtime_ignores_legacy_identity_only_free_blocker(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        welcome = {
            "decision": "ASK_ENTRY_TYPE",
            "readiness": {
                "freeReady": False,
                "erc8004_identity": False,
                "freeRoom": {"ok": False, "missing": ["erc8004_identity"]},
            },
            "rooms": [
                {"entryType": "paid", "playerCount": 0},
                {"entryType": "free", "playerCount": 4},
            ],
        }

        old_fallback = os.environ.get("CLAW_ROYALE_FREE_FALLBACK_ENABLED")
        try:
            os.environ["CLAW_ROYALE_FREE_FALLBACK_ENABLED"] = "true"
            self.assertFalse(claw_runtime.readiness_blocks_free(welcome))
            self.assertEqual(claw_runtime.hello_frame(config, welcome), {"type": "hello", "entryType": "free"})
        finally:
            if old_fallback is None:
                os.environ.pop("CLAW_ROYALE_FREE_FALLBACK_ENABLED", None)
            else:
                os.environ["CLAW_ROYALE_FREE_FALLBACK_ENABLED"] = old_fallback

    def test_claw_runtime_allows_free_fallback_without_balance_or_identity(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        account = {
            "ok": True,
            "balance": 0,
            "readiness": {
                "walletAddress": True,
                "scWallet": True,
                "whitelistApproved": True,
                "identity": False,
                "freeReady": False,
                "freeRoom": {"ok": False, "missing": ["NO_IDENTITY"]},
                "paidReady": False,
            },
        }

        self.assertFalse(claw_runtime.account_paid_ready(account))
        self.assertFalse(claw_runtime.account_identity_ready(account))
        self.assertTrue(claw_runtime.account_free_ready(account))
        self.assertEqual(claw_runtime.join_blocker_for_account(config, account), "")

    def test_claw_runtime_blocks_explicit_non_identity_free_readiness_failure(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="free")
        account = {
            "ok": True,
            "readiness": {
                "identity": False,
                "freeRoom": {"ok": False, "missing": ["NOT_PRIMARY_AGENT"]},
            },
        }

        self.assertFalse(claw_runtime.account_free_ready(account))
        self.assertIn("free join blocked", claw_runtime.join_blocker_for_account(config, account))

    def test_claw_runtime_allows_paid_join_when_paid_ready(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        account = {
            "ok": True,
            "balance": 500,
            "readiness": {
                "walletAddress": True,
                "scWallet": True,
                "whitelistApproved": True,
                "identity": False,
                "paidReady": True,
            },
        }

        self.assertTrue(claw_runtime.account_paid_ready(account))
        self.assertEqual(claw_runtime.join_blocker_for_account(config, account), "")

    def test_claw_runtime_allows_free_fallback_when_paid_balance_is_not_ready(self) -> None:
        config = claw_runtime.ClawRuntimeConfig(api_key="mr_test", mode="offchain")
        account = {
            "ok": True,
            "balance": 0,
            "readiness": {
                "walletAddress": True,
                "scWallet": True,
                "whitelistApproved": True,
                "identity": True,
                "paidReady": False,
            },
        }

        with mock.patch.dict(os.environ, {"CLAW_ROYALE_FREE_FALLBACK_ENABLED": "true"}):
            blocker = claw_runtime.join_blocker_for_account(config, account)

        self.assertEqual(blocker, "")
        self.assertEqual(
            claw_runtime.hello_frame(
                config,
                {
                    "decision": "ASK_ENTRY_TYPE",
                    "availableGames": [
                        {
                            "entryType": "paid",
                            "gameId": "paid-ready-1",
                            "playerCount": 9,
                            "requiredPlayers": 10,
                        },
                        {"entryType": "free", "gameId": "free-1", "playerCount": 1},
                    ],
                },
                paid_account_ready=False,
            ),
            {"type": "hello", "entryType": "free"},
        )

    def test_claw_runtime_does_not_act_when_can_act_false(self) -> None:
        payload = {"type": "agent_view", "data": {"gameId": "g1", "canAct": False, "view": {"canAct": False}}}

        self.assertFalse(claw_runtime.wants_action(payload, claw_runtime.unwrap_snapshot(payload)))

    def test_claw_runtime_treats_game_already_over_as_terminal(self) -> None:
        self.assertTrue(
            claw_runtime.is_terminal_game_error(
                "engine dispatch: game already ended"
            )
        )
        self.assertTrue(claw_runtime.is_terminal_game_error("GAME_ALREADY_OVER"))
        self.assertTrue(claw_runtime.is_terminal_game_error("AGENT_DEAD"))
        self.assertFalse(claw_runtime.is_terminal_game_error("ACTION_COOLDOWN"))

    def test_claw_runtime_records_balance_delta_for_completed_games(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        old_account = claw_runtime.account_status_summary
        balances = iter([100.0, 120.0])

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp

                def fake_account(config):  # type: ignore[no-untyped-def]
                    return {"ok": True, "balance": next(balances), "readiness": {}, "currentGames": []}

                claw_runtime.account_status_summary = fake_account  # type: ignore[assignment]
                config = claw_runtime.ClawRuntimeConfig(api_key="key")
                claw_runtime.record_account_balance(config, stage="connect")
                claw_runtime.record_account_balance(config, stage="game_ended")
                status = runtime_state.read_json(runtime_state.claw_runtime_status_file())
        finally:
            claw_runtime.account_status_summary = old_account  # type: ignore[assignment]
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(status["games_completed"], 1)
        self.assertEqual(status["last_balance_delta"], 20.0)
        self.assertEqual(status["average_balance_delta_per_game"], 20.0)
        self.assertEqual(status["games_needed_for_1000_per_day"], 50)

    def test_runtime_state_can_clear_stale_game_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
            try:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.remember_game_id("stale-game")
                self.assertEqual(runtime_state.stored_game_id(), "stale-game")
                runtime_state.clear_game_id()
                self.assertEqual(runtime_state.stored_game_id(), "")
            finally:
                if old_memory_dir is None:
                    os.environ.pop("CERBERUS_MEMORY_DIR", None)
                else:
                    os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

    def test_runtime_state_cache_write_failures_do_not_crash_callers(self) -> None:
        old_write_json = runtime_state.write_json
        try:
            def failing_write_json(path, payload):  # type: ignore[no-untyped-def]
                raise OSError("disk full")

            runtime_state.write_json = failing_write_json  # type: ignore[assignment]

            runtime_state.update_claw_runtime_status(state="reconnecting")
            runtime_state.remember_game_id("game-1")
            messages = runtime_state.append_stream_chat({"author": "Hellion", "message": "still alive"})
        finally:
            runtime_state.write_json = old_write_json  # type: ignore[assignment]

        self.assertEqual(messages[-1]["message"], "still alive")

    def test_runtime_state_atomic_write_preserves_previous_cache_on_replace_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "runtime.json"
            runtime_state.write_json(path, {"state": "healthy", "count": 1})
            original = path.read_text(encoding="utf-8")
            with mock.patch("memory_system.Path.replace", side_effect=OSError("replace blocked")):
                ok = runtime_state.write_json(path, {"state": "corrupt", "count": 2})
            final_text = path.read_text(encoding="utf-8")
            reloaded = runtime_state.read_json(path)

        self.assertFalse(ok)
        self.assertEqual(final_text, original)
        self.assertEqual(reloaded["state"], "healthy")
        self.assertEqual(reloaded["count"], 1)

    def test_owner_messages_persist_under_memory_dir(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                messages = runtime_state.append_owner_message(
                    {"kind": "owner_command", "text": "prioritize profitable games"}
                )
                reloaded = runtime_state.owner_messages()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(messages[-1]["text"], "prioritize profitable games")
        self.assertEqual(reloaded[-1]["kind"], "owner_command")

    def test_autonomy_suggestions_persist_under_memory_dir(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.append_match_evidence({"kind": "turn_observation", "outcome": {"message": "TARGET_BLOCKED"}})
                runtime_state.append_suggested_edit(
                    {
                        "detector": "runtime.target_blocked",
                        "title": "Add blocked target penalty",
                        "file": "src/decision_engine.py",
                        "symptom": "target blocked",
                        "suggested_change": "penalize repeated blocked target",
                        "priority": "high",
                    }
                )
                evidence = runtime_state.match_evidence()
                edits = runtime_state.suggested_edits()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(evidence[-1]["outcome"]["message"], "TARGET_BLOCKED")
        self.assertEqual(edits[-1]["detector"], "runtime.target_blocked")

    def test_suggested_edits_can_be_reviewed_privately(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                edits = runtime_state.append_suggested_edit(
                    {
                        "detector": "runtime.target_blocked",
                        "title": "Add blocked target penalty",
                        "file": "src/decision_engine.py",
                        "symptom": "target blocked",
                        "suggested_change": "penalize repeated blocked target",
                        "priority": "high",
                    }
                )
                result = runtime_state.update_suggested_edit_status(edits[-1]["id"], "approved", note="ship it")
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(result["ok"])
        self.assertEqual(result["edit"]["status"], "approved")
        self.assertEqual(result["edit"]["review_note"], "ship it")

    def test_postgame_hardening_promotes_rules_and_redacts_compiled_lessons(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                for _ in range(2):
                    runtime_state.append_match_evidence(
                        {
                            "state": {"terrain": "ruin", "visible_agents": 1, "visible_monsters": 0, "visible_items": 0},
                            "action": {"type": "attack", "targetId": "enemy-1"},
                            "outcome": {"message": "TARGET_BLOCKED"},
                        }
                    )
                edits = runtime_state.append_suggested_edit(
                    {
                        "detector": "runtime.target_blocked",
                        "title": "Add blocked target penalty",
                        "file": "src/decision_engine.py",
                        "symptom": "target blocked",
                        "suggested_change": "penalize repeated blocked target",
                        "priority": "high",
                    }
                )
                runtime_state.update_suggested_edit_status(edits[-1]["id"], "approved", note="ship it")
                memory = CompactMemoryStore(
                    path=Path(tmp) / "memory.compact.json",
                    encrypted_path=Path(tmp) / "memory.compact.vault.json",
                ).load()
                dossiers = AgentDossierStore(
                    path=Path(tmp) / "agent_dossiers.compact.json",
                    encrypted_path=Path(tmp) / "agent_dossiers.compact.vault.json",
                ).load()
                result = postgame_hardening.run_postgame_hardening_pass(
                    memory=memory,
                    dossiers=dossiers,
                    rules_path=Path(tmp) / "hardened_strategy_rules.json",
                )
                rules_file_exists = Path(result["rules_path"]).exists()
                reloaded = CompactMemoryStore(
                    path=Path(tmp) / "memory.compact.json",
                    encrypted_path=Path(tmp) / "memory.compact.vault.json",
                ).load()
                stored_edits = runtime_state.suggested_edits()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(result["ok"])
        self.assertEqual(result["approved_suggestions_hardened"], 1)
        self.assertGreaterEqual(result["redacted_lessons"], 1)
        self.assertTrue(rules_file_exists)
        self.assertTrue(all("target_blocked" not in str(item).lower() for item in reloaded.data.get("lessons", [])))
        self.assertEqual(stored_edits[-1]["status"], "hardened")
        self.assertTrue(result["rules"]["cross_agent_dossier_learning"])
        self.assertTrue(result["rules"]["bounded_public_persona"])
        self.assertTrue(result["rules"]["external_wisdom_validation_required"])
        self.assertTrue(result["rules"]["heartbeat_lightweight_only"])
        self.assertTrue(result["rules"]["postgame_batch_window"])
        self.assertTrue(result["rules"]["social_post_requires_trigger"])
        self.assertTrue(result["rules"]["social_focus_submolts"])
        self.assertTrue(result["rules"]["layered_memory_hierarchy"])
        self.assertGreaterEqual(result["voice_lab_soundbites"], 1)
        self.assertEqual(result["memory_policy"]["profile_name"], "layered_compact_memory")
        self.assertIn("dossiers", result["memory_policy"]["tier_order"])
        self.assertTrue(result["memory_policy"]["lesson_redaction_after_hardening"])
        self.assertGreater(result["scheduler_policy"]["heartbeat_budget_ms"], 0)
        self.assertIn("strategy hardening", result["scheduler_policy"]["heartbeat_blocked_work"])
        self.assertTrue(result["scheduler_policy"]["heartbeat_lightweight_only"])
        self.assertIn("compile_lessons", result["scheduler_policy"]["postgame_order"])
        self.assertEqual(result["social_policy"]["default_triggers"]["match_summary"], "postgame")
        self.assertEqual(result["sample_social_tones"]["kill_taunt"], "playful_predator")
        self.assertIn("event trigger present", result["sample_social_prerequisites"]["kill_taunt"])
        self.assertTrue(result["tagging_rules"]["dedupe_tags_per_post"])

    def test_postgame_hardening_turns_social_stack_into_tagged_queue(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                dossiers = AgentDossierStore(
                    path=Path(tmp) / "agent_dossiers.compact.json",
                    encrypted_path=Path(tmp) / "agent_dossiers.compact.vault.json",
                ).load()
                dossiers.record_social_profile("enemy-9", handle="@nemesis")
                dossiers.record_killed_us("enemy-9", name="Nemesis")
                dossiers.record_killed_us("enemy-9", name="Nemesis")
                dossiers.save()
                runtime_state.append_social_event(
                    {
                        "kind": "killed_by_rival",
                        "agent_id": "enemy-9",
                        "agent_name": "Nemesis",
                        "moltybook_handle": "@nemesis",
                        "detail": "repeat_killer_watch",
                    }
                )
                result = postgame_hardening.run_postgame_hardening_pass(
                    memory=CompactMemoryStore(
                        path=Path(tmp) / "memory.compact.json",
                        encrypted_path=Path(tmp) / "memory.compact.vault.json",
                    ).load(),
                    dossiers=AgentDossierStore(
                        path=Path(tmp) / "agent_dossiers.compact.json",
                        encrypted_path=Path(tmp) / "agent_dossiers.compact.vault.json",
                    ).load(),
                    rules_path=Path(tmp) / "hardened_strategy_rules.json",
                )
                queue = social_runtime.social_queue()
                events = runtime_state.social_event_stack()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertTrue(result["ok"])
        self.assertEqual(result["social_drafts"], 1)
        self.assertEqual(queue[-1]["type"], "moltybook_draft")
        self.assertIn("@nemesis", queue[-1]["content"])
        self.assertEqual(events[-1]["status"], "drafted")

    def test_owner_balance_alarm_gets_runtime_diagnostic_response(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.update_claw_runtime_status(
                    state="playing",
                    mode="free",
                    games_completed=85,
                    average_balance_delta_per_game=0.08,
                    account={"balance": 46, "readiness": {"paidReady": False}},
                    join_readiness={"paidRoom": {"ok": True, "mode": {"onchain": True}}},
                    last_error="COOLDOWN_ACTIVE",
                )
                response = render_app.diagnostic_owner_response(
                    {"kind": "owner_command", "text": "balance hasn't changed, what is going on"}
                )
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertIsNotNone(response)
        self.assertEqual(response["status"], "diagnostic")
        self.assertIn("balance=46", response["text"])
        self.assertIn("games=85", response["text"])

    def test_shared_public_voice_defaults_come_from_external_wisdom(self) -> None:
        self.assertIn("emergency plans", owner_command_cortex.taunt_message({"text": "taunt"}))
        self.assertIn("intermission", claw_runtime.public_action_thought({"type": "rest"}).lower())

    def test_dashboard_is_game_map_command_center(self) -> None:
        html = render_app.dashboard_html().decode("utf-8")

        self.assertIn('id="game-map"', html)
        self.assertIn('id="owner-form"', html)
        self.assertIn('id="paid-ready"', html)
        self.assertIn('id="healthz"', html)
        self.assertIn('fetch("/admin/owner-message"', html)
        self.assertIn('fetchJson("/admin/suggested-edits"', html)
        self.assertIn('fetchJson("/admin/suggested-edit-status"', html)
        self.assertIn('id="suggested-edits"', html)
        self.assertIn('id="public-thought"', html)
        self.assertIn('cache = "no-store"', html)
        self.assertIn("overflow: hidden", html)
        self.assertIn("<aside", html)

    def test_stats_falls_back_to_runtime_status_game_id_when_current_game_file_is_missing(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.update_claw_runtime_status(
                    current_game_id="game-live-7",
                    state="playing",
                    last_public_thought="Hellion scouts forward. Standing still is for statues.",
                    last_snapshot={"game_id": "game-live-7", "hp": 88, "ep": 4},
                )
                payload = render_app.stats()
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(payload["current_game_id"], "game-live-7")
        self.assertTrue(payload["spectate_url"].endswith("/game-live-7"))
        self.assertIn("Hellion", payload["public_thought"])

    def test_render_game_id_helpers_sanitize_untrusted_values(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                runtime_state.remember_game_id('game-live-7" onload="alert(1)')
                stale_active = render_app.active_runtime_game_id({})
                live_active = render_app.active_runtime_game_id({"state": "playing"})
                query = render_app.query_game_id("gameId=game-live-8%22%20onload%3D%22alert(1)")
                spectate = render_app.spectate_url('game-live-9" onload="alert(1)')
        finally:
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(stale_active, "")
        self.assertEqual(live_active, "game-live-7onloadalert1")
        self.assertEqual(query, "game-live-8onloadalert1")
        self.assertTrue(spectate.endswith("/game-live-9onloadalert1"))

    def test_render_root_serves_dashboard_and_healthz_stays_json(self) -> None:
        sent = []

        class FakeHandler(render_app.CerberusHandler):
            def __init__(self, path):  # type: ignore[no-untyped-def]
                self.path = path

            def _send_html(self, body, *, status=200):  # type: ignore[no-untyped-def]
                sent.append(("html", status, body))

            def _send(self, body, *, status=200):  # type: ignore[no-untyped-def]
                sent.append(("json", status, body))

        FakeHandler("/").do_GET()
        FakeHandler("/healthz").do_GET()

        self.assertEqual(sent[0][0], "html")
        self.assertIn(b"Hellion Dashboard", sent[0][2])
        self.assertEqual(sent[1], ("json", 200, {"ok": True, "service": "cerberus"}))

    def test_render_head_requests_return_headers_without_body(self) -> None:
        sent = []

        class FakeHandler(render_app.CerberusHandler):
            def __init__(self, path):  # type: ignore[no-untyped-def]
                self.path = path

            def _send_empty(self, content_type, *, status=200):  # type: ignore[no-untyped-def]
                sent.append((status, content_type))

        FakeHandler("/healthz").do_HEAD()
        FakeHandler("/dashboard").do_HEAD()
        FakeHandler("/missing").do_HEAD()

        self.assertEqual(sent[0], (200, "application/json"))
        self.assertEqual(sent[1], (200, "text/html; charset=utf-8"))
        self.assertEqual(sent[2], (404, "application/json"))

    def test_render_launch_report_endpoint_is_pin_guarded(self) -> None:
        old_pin = os.environ.get("CERBERUS_PIN")
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        sent = []

        class FakeHeaders(dict):
            def get(self, key, default=None):  # type: ignore[no-untyped-def]
                return super().get(key, default)

        class FakeHandler(render_app.CerberusHandler):
            def __init__(self, pin):  # type: ignore[no-untyped-def]
                self.path = "/admin/launch-report"
                self.headers = FakeHeaders({"X-Cerberus-Pin": pin})

            def _read_json(self):  # type: ignore[no-untyped-def]
                return {}

            def _send(self, body, *, status=200):  # type: ignore[no-untyped-def]
                sent.append((status, body))

        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["CERBERUS_PIN"] = "123456"
                os.environ["CERBERUS_MEMORY_DIR"] = tmp
                FakeHandler("bad").do_POST()
                FakeHandler("123456").do_POST()
        finally:
            if old_pin is None:
                os.environ.pop("CERBERUS_PIN", None)
            else:
                os.environ["CERBERUS_PIN"] = old_pin
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertEqual(sent[0][0], 401)
        self.assertEqual(sent[0][1]["error"], "invalid_pin")
        self.assertIn(sent[1][0], {200, 503})
        self.assertIn("runtime", sent[1][1])
        self.assertIn("profit", sent[1][1])
        self.assertIn("env_lint", sent[1][1])

    def test_cross_spinal_cord_reports_side_effect_results_and_uses_moltbook_key(self) -> None:
        old_identity = sys.modules.get("identity_vault")
        old_moltybook = sys.modules.get("moltybook_client")
        calls = []

        class FakeVault:
            data = {"moltbook": {"api_key": "mb-test"}}

            def load(self):  # type: ignore[no-untyped-def]
                return self

        class FakeClient:
            def __init__(self, api_key="", enabled=False):  # type: ignore[no-untyped-def]
                self.api_key = api_key
                self.enabled = enabled

            def post_draft(self, draft):  # type: ignore[no-untyped-def]
                calls.append((self.api_key, self.enabled, draft))
                return {"ok": True, "posted": True}

        sys.modules["identity_vault"] = types.SimpleNamespace(IdentityVault=FakeVault)  # type: ignore[assignment]
        sys.modules["moltybook_client"] = types.SimpleNamespace(MoltyBookClient=FakeClient)  # type: ignore[assignment]
        try:
            result = cross_spinal_cord._process_moltybook(
                {"type": "moltybook_draft", "content": "Hellion heard the bell."}
            )
        finally:
            if old_identity is None:
                sys.modules.pop("identity_vault", None)
            else:
                sys.modules["identity_vault"] = old_identity
            if old_moltybook is None:
                sys.modules.pop("moltybook_client", None)
            else:
                sys.modules["moltybook_client"] = old_moltybook

        self.assertEqual(result, {"ok": True, "posted": True})
        self.assertEqual(calls[0][0], "mb-test")
        self.assertTrue(calls[0][1])

    def test_cross_spinal_cord_unimplemented_forge_swap_is_reported(self) -> None:
        result = cross_spinal_cord._process_forge_swap({"type": "forge_swap"})

        self.assertFalse(result["ok"])
        self.assertIn(result["reason"], {"missing_agent_private_key", "not_implemented", "forge_swap_side_effect_failed"})

    def test_render_readiness_reports_memory_write_failure_without_crashing(self) -> None:
        old_memory_dir = os.environ.get("CERBERUS_MEMORY_DIR")
        old_store = render_app.LongTermMemoryStore
        try:
            with tempfile.TemporaryDirectory() as tmp:
                blocked = Path(tmp) / "blocked"
                os.environ["CERBERUS_MEMORY_DIR"] = str(blocked)

                class FailingStore:
                    def stats(self):  # type: ignore[no-untyped-def]
                        return {"items": 0, "bytes": 0}

                render_app.LongTermMemoryStore = FailingStore  # type: ignore[assignment]
                old_mkdir = Path.mkdir
                def failing_mkdir(self, parents=False, exist_ok=False):  # type: ignore[no-untyped-def]
                    if self == blocked:
                        raise OSError("write blocked")
                    return old_mkdir(self, parents=parents, exist_ok=exist_ok)

                Path.mkdir = failing_mkdir  # type: ignore[assignment]
                try:
                    status = render_app.readiness()
                finally:
                    Path.mkdir = old_mkdir  # type: ignore[assignment]
        finally:
            render_app.LongTermMemoryStore = old_store  # type: ignore[assignment]
            if old_memory_dir is None:
                os.environ.pop("CERBERUS_MEMORY_DIR", None)
            else:
                os.environ["CERBERUS_MEMORY_DIR"] = old_memory_dir

        self.assertFalse(status["ok"])
        self.assertFalse(status["memory_writable"])
        self.assertIn("write blocked", status["memory_error"])

    def test_render_leave_current_game_uses_first_successful_candidate(self) -> None:
        old_key = os.environ.get("CLAW_ROYALE_API_KEY")
        old_version = os.environ.get("CLAW_ROYALE_VERSION")
        old_request = render_app.requests.request
        calls = []

        class Response:
            def __init__(self, status_code, text):  # type: ignore[no-untyped-def]
                self.status_code = status_code
                self.text = text

        def fake_request(method, url, **kwargs):  # type: ignore[no-untyped-def]
            calls.append((method, url, kwargs))
            if url.endswith("/games/game-1/cancel"):
                return Response(200, '{"ok":true}')
            return Response(404, "not found")

        try:
            os.environ["CLAW_ROYALE_API_KEY"] = "mr_test"
            os.environ["CLAW_ROYALE_VERSION"] = "1.9.0"
            render_app.requests.request = fake_request  # type: ignore[assignment]

            result = render_app.leave_current_game("game-1")
        finally:
            render_app.requests.request = old_request  # type: ignore[assignment]
            if old_key is None:
                os.environ.pop("CLAW_ROYALE_API_KEY", None)
            else:
                os.environ["CLAW_ROYALE_API_KEY"] = old_key
            if old_version is None:
                os.environ.pop("CLAW_ROYALE_VERSION", None)
            else:
                os.environ["CLAW_ROYALE_VERSION"] = old_version

        self.assertTrue(result["ok"])
        self.assertEqual(result["path"], "/games/game-1/cancel")
        self.assertEqual(calls[1][2]["headers"]["X-API-Key"], "mr_test")

    def test_render_leave_current_game_reports_failed_candidates(self) -> None:
        old_key = os.environ.get("CLAW_ROYALE_API_KEY")
        old_request = render_app.requests.request
        old_ws_leave = render_app.websocket_leave_current_game

        class Response:
            status_code = 404
            text = "not found"

        try:
            os.environ["CLAW_ROYALE_API_KEY"] = "mr_test"
            render_app.requests.request = lambda *args, **kwargs: Response()  # type: ignore[assignment]
            render_app.websocket_leave_current_game = lambda game_id, headers: {"ok": False, "attempts": [{"method": "WS", "error": "closed"}]}  # type: ignore[assignment]

            result = render_app.leave_current_game("game-1<script>")
        finally:
            render_app.requests.request = old_request  # type: ignore[assignment]
            render_app.websocket_leave_current_game = old_ws_leave  # type: ignore[assignment]
            if old_key is None:
                os.environ.pop("CLAW_ROYALE_API_KEY", None)
            else:
                os.environ["CLAW_ROYALE_API_KEY"] = old_key

        self.assertFalse(result["ok"])
        self.assertEqual(result["game_id"], "game-1script")
        self.assertEqual(result["error"], "no_leave_route_accepted")
        self.assertGreaterEqual(len(result["attempts"]), 3)

    def test_claw_paid_join_typed_data_signer_returns_signature(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        payload = {
            "type": "sign_required",
            "data": {
                "requestId": "req-1",
                "typedData": {
                    "types": {
                        "EIP712Domain": [
                            {"name": "name", "type": "string"},
                            {"name": "version", "type": "string"},
                            {"name": "chainId", "type": "uint256"},
                        ],
                        "JoinGame": [
                            {"name": "agent", "type": "address"},
                            {"name": "gameId", "type": "string"},
                        ],
                    },
                    "primaryType": "JoinGame",
                    "domain": {"name": "ClawRoyale", "version": "1", "chainId": 612055},
                    "message": {"agent": account.address, "gameId": "game-1"},
                },
            },
        }

        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(signed["type"], "signature")
        self.assertEqual(signed["requestId"], "req-1")
        self.assertEqual(signed["signerAddress"], account.address)
        self.assertEqual(signed["signingMode"], "typed_data")
        self.assertTrue(str(signed["signature"]).startswith("0x"))

    def test_claw_paid_join_signer_preserves_join_intent_id(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        payload = {
            "type": "sign_required",
            "data": {
                "joinIntentId": "join-1",
                "typedData": {
                    "types": {
                        "EIP712Domain": [{"name": "name", "type": "string"}],
                        "JoinGame": [{"name": "agent", "type": "address"}],
                    },
                    "primaryType": "JoinGame",
                    "domain": {"name": "ClawRoyale"},
                    "message": {"agent": account.address},
                },
            },
        }

        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(signed["joinIntentId"], "join-1")

    def test_claw_paid_join_signer_accepts_plain_message_frame(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        payload = {
            "type": "sign_required",
            "deadline": "2026-06-10T19:39:22.454Z",
            "gameId": "game-1",
            "joinIntentId": "join-plain-1",
            "message": "Join paid Claw Royale game game-1 as Hellion.",
        }

        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(signed["type"], "signature")
        self.assertEqual(signed["joinIntentId"], "join-plain-1")
        self.assertEqual(signed["signerAddress"], account.address)
        self.assertEqual(signed["signingMode"], "plain_message")
        self.assertEqual(signed["messageLength"], len(payload["message"]))
        self.assertEqual(signed["messagePreview"], payload["message"])
        self.assertTrue(str(signed["signature"]).startswith("0x"))

    def test_claw_paid_join_signer_parses_json_typed_data_message(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        typed_data = {
            "types": {
                "EIP712Domain": [{"name": "name", "type": "string"}],
                "JoinGame": [{"name": "agent", "type": "address"}],
            },
            "primaryType": "JoinGame",
            "domain": {"name": "ClawRoyale"},
            "message": {"agent": account.address},
        }
        payload = {
            "type": "sign_required",
            "joinIntentId": "join-json-1",
            "message": json.dumps(typed_data, separators=(",", ":")),
        }

        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(signed["signingMode"], "typed_data")
        self.assertEqual(signed["joinIntentId"], "join-json-1")
        self.assertTrue(str(signed["signature"]).startswith("0x"))

    def test_claw_paid_join_signer_infers_crossmainnet_typed_data(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        message = {
            "domain": {
                "chainId": 612055,
                "name": "ArenaPaid",
                "verifyingContract": "0x8f705417C2a11446e93f94cbe84F476572EE90Ed",
                "version": "1",
            },
            "message": {
                "agentId": "28953448436016",
                "deadline": 1781123101,
                "player": account.address,
            },
        }
        payload = {"type": "sign_required", "joinIntentId": "join-cross-1", "message": json.dumps(message)}

        challenge = claw_signing.signing_challenge_from_payload(payload)
        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(challenge.mode, "typed_data")
        self.assertEqual(challenge.typed_data["domain"]["chainId"], 612055)
        self.assertEqual(signed["signingMode"], "typed_data")
        self.assertEqual(signed["joinIntentId"], "join-cross-1")
        self.assertTrue(str(signed["signature"]).startswith("0x"))

    def test_claw_paid_join_signer_infers_nested_message_object(self) -> None:
        Account = self._require_eth_account()
        account = Account.create()
        payload = {
            "type": "sign_required",
            "joinIntentId": "join-object-1",
            "message": {
                "domain": {
                    "chainId": 612055,
                    "name": "ArenaPaid",
                    "verifyingContract": "0x8f705417C2a11446e93f94cbe84F476572EE90Ed",
                    "version": "1",
                },
                "message": {
                    "agentId": "28953448436016",
                    "deadline": 1781123101,
                    "player": account.address,
                },
            },
        }

        challenge = claw_signing.signing_challenge_from_payload(payload)
        signed = claw_signing.sign_typed_data_frame(payload, private_key=account.key.hex())

        self.assertEqual(challenge.mode, "typed_data")
        self.assertEqual(signed["signingMode"], "typed_data")
        self.assertEqual(signed["joinIntentId"], "join-object-1")

    def test_claw_sign_submit_frame_keeps_only_protocol_fields(self) -> None:
        frame = claw_runtime.sign_submit_frame(
            {
                "signature": "0xsig",
                "joinIntentId": "join-1",
                "signerAddress": "0x" + "1" * 40,
                "signingMode": "plain_message",
                "messageHash": "0xhash",
            }
        )

        self.assertEqual(frame, {"type": "sign_submit", "signature": "0xsig", "joinIntentId": "join-1"})

    def test_claw_version_single_source_uses_env_override(self) -> None:
        old = os.environ.get("CLAW_ROYALE_VERSION")
        try:
            os.environ["CLAW_ROYALE_VERSION"] = "9.9.9"
            self.assertEqual(claw_config.active_claw_version(), "9.9.9")
        finally:
            if old is None:
                os.environ.pop("CLAW_ROYALE_VERSION", None)
            else:
                os.environ["CLAW_ROYALE_VERSION"] = old

    def test_claw_version_reconcile_updates_process_env_on_drift(self) -> None:
        old_fetch = claw_config.fetch_live_claw_version
        old = os.environ.get("CLAW_ROYALE_VERSION")
        try:
            os.environ["CLAW_ROYALE_VERSION"] = "1.9.0"
            claw_config.fetch_live_claw_version = lambda api_base=None: "1.9.1"  # type: ignore[assignment]

            self.assertEqual(claw_config.reconcile_claw_version(), "1.9.1")
            self.assertEqual(os.environ["CLAW_ROYALE_VERSION"], "1.9.1")
        finally:
            claw_config.fetch_live_claw_version = old_fetch  # type: ignore[assignment]
            if old is None:
                os.environ.pop("CLAW_ROYALE_VERSION", None)
            else:
                os.environ["CLAW_ROYALE_VERSION"] = old


if __name__ == "__main__":
    unittest.main()

