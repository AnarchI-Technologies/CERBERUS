from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from agent_dossiers import AgentDossierStore
from core_loop import cerberus_tick, normalize_action
import identity_bootstrap
import moltbook_claim_assistant
import x_oauth
from identity_bootstrap import (
    ensure_agentmail,
    ensure_claw_account,
    ensure_moltbook,
    ensure_molty_wallet,
    ensure_wallets,
    BootstrapResult,
)
from isolated_runtime import IsolatedCerberusInstance
from identity_vault import DEFAULT_PUBLIC_NAME, empty_identity
from knowledge_base import KnowledgeBase
from memory_system import CompactMemoryStore
from moltbook_claim_assistant import extract_moltbook_claims, stored_claim, verification_text
from moltybook_client import MoltyBookClient, process_social_side_effects
from onboarding_clients import ClawRoyaleClient, build_claw_siwe_message
from onboarding_clients import OnboardingAPIError, _unwrap
from progression_cortex import ProgressionCortex
from risk_engine import progression_value_at_risk
from social_cortex import SocialCortex
from turn_state_model import TurnState
from wallet_identity import wallet_for_purpose
from x_oauth import authorization_url, parse_callback_url, pkce_pair


class HardeningTests(unittest.TestCase):
    def _isolated(self, tmp: str) -> IsolatedCerberusInstance:
        return IsolatedCerberusInstance.create(Path(tmp) / "isolated")

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

    def test_social_side_effect_failures_are_captured_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            isolated = self._isolated(tmp)
            state = TurnState.from_snapshot(
                {
                    "view": {
                        "self": {"id": "me", "hp": 80, "ep": 4, "inventory": [{"id": "meg", "typeId": "megaphone"}]},
                        "currentRegion": {"id": "r1"},
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

    def test_moltbook_inbox_claim_short_circuits_without_inbox(self) -> None:
        claims = extract_moltbook_claims({"messages": []})
        self.assertEqual(claims["claim_urls"], [])
        self.assertEqual(claims["codes"], [])

        identity = empty_identity()
        self.assertEqual(
            moltbook_claim_assistant.inbox_claim(identity),
            {"claim_urls": [], "urls": [], "codes": []},
        )

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


if __name__ == "__main__":
    unittest.main()
