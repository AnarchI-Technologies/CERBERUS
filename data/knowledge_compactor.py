"""
Build compact Cerberus Agent Knowledge (.cak) from Markdown audit files.

The .md files remain the human audit trail. The agent should load the .cak
artifact: short fact strings, source hashes, and enough provenance to avoid
hardcoding stale truths.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCES = [
    ROOT / "claw_royale_v1_9_truths.md",
    ROOT / "forge_token_deployer_truths.md",
    ROOT / "cerberus_mmmmm_knowledge_digest.md",
    ROOT / "legacy_brain_analysis.md",
]
DEFAULT_OUT = ROOT / "cerberus_agent_knowledge.cak"
DEFAULT_GZ_OUT = ROOT / "cerberus_agent_knowledge.cak.gz"
SOURCE_LINE_TERMS = re.compile(
    r"alert|ruin|relic|pack|shop|reforge|guardian|action|cooldown|"
    r"thought|websocket|endpoint|wallet|identity|memory|secret|"
    r"death|zone|combat|weapon|item|economy|settlement|loadout",
    re.I,
)


CURATED_FACTS = [
    "F|meta.brand|current=Claw Royale;legacy=Molty Royale;moltyroyale.com redirects to clawroyale.ai",
    "F|meta.source_precedence|1=live_claw_docs_api;2=project_bootstrap;3=legacy_brain.py;4=rss220426v152;5=rss200426v151",
    "F|meta.version|source=src/claw_config.py+GET_/api/version;runtime_reconciles_before_game=true;last_known_major=1.9",
    "F|runtime.api|base=https://cdn.clawroyale.ai/api;auth=X-API-Key;version_header=X-Version;version_endpoint=GET /api/version",
    "F|runtime.ws_base|rest_uses_api_path=true;websocket_live_host=wss://cdn.clawroyale.ai;join_path=/ws/join;agent_path=/ws/agent",
    "F|runtime.rest.account|POST /accounts returns apiKey once;PUT /accounts/wallet attaches Agent EOA;GET /accounts/me has readiness,currentGames,balance",
    "F|runtime.join|preferred=GET /ws/join;single_socket_becomes_gameplay=true;send_hello_before_helloDeadlineSec;legacy_POST_join_deprecated=true",
    "F|runtime.join_waits|hello_default=15s;free_assignment_about=120s;paid_sign_deadline_about=5min;paid_join_after_tx_about=30s",
    "F|runtime.join.free|hello={type:hello,entryType:free};requires=api_key+ERC8004_identity;not_selected_closes_then_redial=true",
    "F|runtime.join.paid|hello={type:hello,entryType:paid,mode:offchain};requires=whitelist+balance;flow=welcome->hello->sign_required->sign_submit->queued->tx_submitted->joined",
    "F|runtime.ws|gameplay=GET /ws/agent;headers=X-API-Key+X-Version;do_not_append_gameId_or_agentId;one_active_session_per_api_key=true;rate=120msg_min",
    "F|runtime.frames|server=agent_view,turn_advanced,action_result,can_act_changed,event,game_ended,pong;next_agent_view_is_truth=true",
    "F|runtime.events|preS1=ruin_state_changed,alert_gauge_changed,relic_acquired,pack_acquired,relic_dropped,pack_dropped,relic_discarded,pack_discarded,game_settled;poll_inventory_after_game_settled=true",
    "F|action.envelope|current={type:action,data:{type,...},thought:string};thought_max=700;legacy_thought_object=stale",
    "F|action.cooldown|current=30s;cooldown_group=move,explore,attack,use_item,interact,rest;free_actions=pickup,equip,talk,whisper,broadcast",
    "F|action.cost|live_source=agent_view.availableActions.<action>.cost;attack_cost_must_not_use_static_table;legacy_fallback_only_when_live_missing=true",
    "F|action.free|pickup=0ep_no_cd;equip=0ep_no_cd;talk=0ep_no_cd_200chars;whisper=0ep_no_cd_same_region_200chars;broadcast=0ep_no_cd_requires_broadcast_station",
    "F|action.constraints|pickup_inventory_cap=10;explore_ruin_only=true;interact_blocked_in_death_zone=true;after_cooldown_action_wait_for_can_act_changed=true",
    "F|combat.agent|live_source=agent_view.self;do_not_assume_static_stats=true",
    "F|combat.guardian|live_source=agent_view.visibleMonsters;do_not_assume_static_stats=true",
    "F|combat.damage|live_source=references/combat-items.md+agent_view;local_formula_is_heuristic_only=true",
    "F|combat.weapon|live_source=inventory.atkBonus+equippedWeapon+availableActions;static_name_table_is_legacy_fallback_only=true",
    "F|combat.armor|live_source=agent_view.self.equippedArmor.defBonus+inventory.defBonus;equip_action_shared_with_weapons=true",
    "F|combat.range|visible_not_attackable=true;verify_distance<=weapon_range_or_same_region_before_attack;out_of_range_attack_wastes_turn=true",
    "F|items.recovery|live_source=inventory.hpRestore+inventory.epRestore;do_not_assume_static_name_values=true",
    "F|safety.deathzone|starts_day2;expands_every_18h_or_3_turns;damage=1.34hp_s;avoid_pendingDeathzones",
    "F|safety.alert|explore=+2;complete_ruin=+4;threshold=10;below_10_no_decay;alerted_end_turn=-4;guardian_target_when_alerted=true",
    "F|progression.ruin|preS1=true;locations_random;type_visible=relic_or_pack;gauge_max=3;base_explore_progress=1;min_progress=1;depleted_after_claim=true",
    "F|progression.ruin_death|death_while_carrying_relic_or_pack_returns_item_to_origin_ruin;not_death_location_drop",
    "F|progression.relic|permanent_if_survive=true;hidden_in_match=true;reveal_on_settlement=true;color=slot_compat_only;affixes=0to3;affixes_stack=true",
    "F|progression.relic.affixes|domains=atk,def,max_hp,max_ep,explore_efficiency,item_atk;positive_or_negative=true",
    "F|progression.inventory|match_relic_cap=5;match_pack_cap=1;lobby_relic_cap=15;lobby_pack_cap=5;excess_auto_discard=true",
    "F|progression.pack|fullSet_requires=main_pack+sub_pack+red+green+blue;sub_pack_required=true;partial_set_bonus=0;sub_effects_half_strength=true;scout_assassin_main_only=true",
    "F|progression.pack.categories|moltz_expert=items_to_smoltz;item_expert=smoltz_to_item_atk;goliath=aoe_lower_atk_more_ep;thorns=damage_reduction_reflect_lower_outgoing;scout=vision_move_efficiency_lower_outgoing",
    "F|progression.loadout|configure_before_join=true;midgame_change=false;mutations_require_Idempotency-Key_UUID=true;fullSet_required_for_effectiveStats",
    "F|progression.loadout_slots|typeIndex_0=red;typeIndex_1=green;typeIndex_2=blue;effectiveStatsPreview=relic_affixes+goliath_atkMultiplier;moltz_item_expert_runtime_only=true",
    "F|progression.inventory_ops|discard_equipped_relic_fails_409=true;discard_active_pack_requires_unset=true;discard_pack_returns_equipped_relics_to_inventory=true",
    "F|progression.events|ruin_state_changed,alert_gauge_changed,relic_acquired,pack_acquired,relic_dropped,pack_dropped,relic_discarded,pack_discarded,game_settled",
    "F|progression.settlement|survivors_keep_relics_packs;eliminated_lose_unclaimed;details_reveal_after_game_settled;poll_inventory_after_settlement",
    "F|economy.free|pool=1000_sMoltz;object_pool=300;guardian_pool=600;guardians=30;guardian_drop=20_sMoltz",
    "F|economy.paid|offchain_entry=500_sMoltz;onchain_entry=500_Moltz;paid_rooms_no_moltz_smoltz_drops=true;cross_reward_disabled_in_docs",
    "F|economy.v1_9.shop|random_pack_ticket=25000_sMoltz;reforge_stone_bundle=3000_sMoltz;profile_ticket=50000_sMoltz;lower_tier_packs_rarer_stronger=true",
    "F|economy.v1_9.pack_odds|category_uniform=20pct_each_5_categories;tier_T1=1of6;tier_T2=2of6;tier_T3=3of6;lower_tier_rarer_stronger=true",
    "F|economy.v1_9.topup|min=1000_MOLTZ;flow=ERC20_approve_then_charge;credit=floor(MOLTZ*rate);remainders_not_credited=true",
    "F|economy.reforge|resource=Reforge_Stone;targets=owned_relics;max_affixes=3;duplicates_stack=true;ops=effect_add,effect_remove,effect_reroll,stat_reroll",
    "F|economy.reforge_odds|effect_reroll=200of221;effect_add=10of221;effect_remove=10of221;stat_reroll=1of221",
    "F|progression.affix_pool|strong_atk=+1to+10;weak_atk=-10to-1;fortified_def=+1to+5;brittle_def=-5to-1;swift_explore=+1;slow_explore=-1;sharp_item_atk=+5to+15;dull_item_atk=-15to-5;sturdy_hp=+1to+10;fragile_hp=-10to-1;vigorous_ep=+1to+2;drained_ep=-2to-1",
    "F|session.autopickup|session_start_auto_pickup_current_region_items=true;auto_equip_highest_effective_atk_weapon=true",
    "F|runtime.errors|VERSION_MISMATCH=refresh_version;NO_IDENTITY=legacy_optional_do_not_block_free;SC_WALLET_NOT_FOUND=create_sc_wallet;AGENT_NOT_WHITELISTED=request_whitelist;INSUFFICIENT_BALANCE=topup;ACTION_COOLDOWN=wait",
    "F|guardian.current|free_count=30;paid_count=8;adjacent_to_ruins=true;stationary=true;target_only_alerted=true;simultaneous_targets=true;curse_disabled=true",
    "F|guardian.legacy|rss_v1_5_2_count=5;direct_hostile=true;free_drop=120_sMoltz;legacy_only=true",
    "F|wallets.model|three_wallets=owner_eoa,clawroyale_wallet,agent_eoa;do_not_send_Moltz_to_agent_wallet=true",
    "F|wallets.readiness|free_requires_api_key;erc8004_optional_since_1_11_2=true;paid_offchain_requires_wallet+scWallet+whitelist+500sMoltz;paid_onchain_requires_wallet+scWallet+whitelist+500Moltz",
    "F|wallets.rewards|no_wallet_no_rewards=true;past_rewards_not_retroactive=true;api_key_shown_once=true",
    "F|identity.erc8004|required_for_free=false;optional_since_1_11_2=true;agentId_means_NFT_tokenId_not_game_uuid;gas_delegated=true",
    "F|forge.auth|default=vendor;client_requires=CLIENT_KEY+CLIENT_SECRET;vendor_no_credentials=true",
    "F|forge.wallet|default=tmp;user_wallet_returns_unsigned_pool_tx;tmp_wallet_deploys_and_creates_pool;tmp_owner_permissions_not_reusable=true",
    "F|forge.agent_token|category=ai_agent;must_use_wallet=user;owner_must_equal_agent_eoa;never_tmp_for_agent_token=true",
    "F|forge.constants|rpc=https://mainnet.crosstoken.io:22001;router=0x7aF414e4d373bb332f47769c8d28A446A0C1a1E8;tokenB=0xDdF8AaA3927b8Fd5684dc2edcc7287EcB0A2122d;trade=https://x.crosstoken.io/forge/token",
    "F|forge.inputs|name,symbol,description,imageUrl,walletAddress,category_required;image_png_jpg_max=1MB;symbols_unique_case_insensitive=true",
    "F|security.local|agent_memory_reads_cak_not_md;memory_no_raw_dumps=true;secret_files_use_vault_with_CERBERUS_PIN=true",
    "F|memory.policy|store_compact_strings=T_turn,L_lesson,F_fact;redact_secret_like_keys;hash_raw_state_not_store_raw_state",
    "F|social.moltybook|drafts_default_not_auto_post;enable_with_CERBERUS_MOLTYBOOK_ENABLED=true;key_env=MOLTYBOOK_API_KEY",
    "F|social.persona|voice=clever_whimsical_tactful_playful;taunt_kills_and_outsmarts=true;avoid_cruelty=true",
    "F|social.secrecy|share_legit_principles;never_share_formulas,deterministic_chains,exact_scores,source_code,secrets",
    "F|social.dossiers|track_encountered_agents;follow_moltybook_handles_when_seen;merge_battlefield_and_social_observations",
    "F|social.validation|public_strategy_claims_are_untrusted;validate_against_rules_and_victory_pathing_before_memory",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def clean_line(line: str) -> str:
    line = line.strip()
    line = re.sub(r"^#{1,6}\s*", "H|", line)
    line = re.sub(r"^[-*]\s+", "", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
    line = re.sub(r"\s+", " ", line)
    return line.strip()


def iter_source_facts(path: Path, *, max_lines: int = 240) -> Iterable[str]:
    if not path.exists():
        return []
    facts: list[str] = []
    section = ""
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = clean_line(raw)
        if not line or line in {"|---|---|", "|---|---:|", "|---|---|---|"}:
            continue
        if line.startswith("H|"):
            section = line[2:96]
            continue
        if len(line) < 12 or not SOURCE_LINE_TERMS.search(line):
            continue
        if len(line) > 260:
            line = line[:259] + "~"
        facts.append(f"M|{path.name}|{section}|{line}")
        if len(facts) >= max_lines:
            break
    return facts


def build_compact_knowledge(sources: list[Path]) -> dict:
    source_meta = []
    facts = list(CURATED_FACTS)
    for path in sources:
        if not path.exists():
            continue
        source_meta.append(
            {
                "p": path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
        facts.extend(iter_source_facts(path))

    deduped = list(dict.fromkeys(facts))
    return {
        "t": "cerberus.agent_knowledge",
        "v": 1,
        "built_at": utc_now(),
        "load_hint": "Read k[] as compact fact strings. Prefer F facts over M markdown traces when they conflict.",
        "sources": source_meta,
        "k": deduped,
    }


def write_artifacts(out: Path, gz_out: Path, data: dict) -> tuple[Path, Path]:
    payload = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    out.write_bytes(payload)
    with gzip.open(gz_out, "wb", compresslevel=9) as fh:
        fh.write(payload)
    return out, gz_out


def load_compact_knowledge(path: str | Path) -> dict:
    p = Path(path)
    raw = p.read_bytes()
    if p.suffix == ".gz":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Cerberus compact knowledge")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--gz-out", default=str(DEFAULT_GZ_OUT))
    parser.add_argument("sources", nargs="*", default=[str(p) for p in DEFAULT_SOURCES])
    args = parser.parse_args()

    sources = [Path(s) for s in args.sources]
    data = build_compact_knowledge(sources)
    out, gz_out = write_artifacts(Path(args.out), Path(args.gz_out), data)
    print(
        json.dumps(
            {
                "out": str(out),
                "gz_out": str(gz_out),
                "facts": len(data["k"]),
                "bytes": out.stat().st_size,
                "gz_bytes": gz_out.stat().st_size,
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
