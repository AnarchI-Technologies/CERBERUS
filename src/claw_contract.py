"""Deterministic Claw Royale gameplay contract aligned through v1.13.1.

Keep volatile API versioning in ``claw_config``. Live OpenAPI and per-snapshot
fields win; static values here are compatibility fallbacks and stable rules.
"""

from __future__ import annotations

from typing import Any


THOUGHT_MAX_CHARS = 700
CHAT_MAX_CHARS = 200
MESSAGE_RATE_LIMIT_PER_MINUTE = 120
TURN_SECONDS = 30
PASSIVE_EP_RECOVERY_PER_TURN = 1
ACTIVE_GAME_LIMITS = {"free": 1, "paid": 1}
REST_API_BASE = "https://cdn.clawroyale.ai/api"
WEBSOCKET_HOST = "wss://cdn.clawroyale.ai"

REST_ENDPOINTS = {
    "version": "GET /api/version",
    "account_create": "POST /accounts",
    "account_wallet": "PUT /accounts/wallet",
    "account_me": "GET /accounts/me",
    "wallet_create": "POST /create/wallet",
    "whitelist_request": "POST /whitelist/request",
    "identity_create": "POST /identity",
    "identity_get": "GET /identity",
    "identity_delete": "DELETE /identity",
    "join_status": "GET /join/status",
    "waiting_games": "GET /games?status=waiting",
    "preseason1_quests": "GET /preseason1/quests",
    "preseason1_daily_quests": "GET /preseason1/daily-quests",
    "preseason1_summary": "GET /preseason1/me/summary",
    "preseason1_quest_claim": "POST /preseason1/quests/:key/claim/:tier",
    "preseason1_daily_claim": "POST /preseason1/daily-quests/:key/claim",
    "redeem": "POST /redeem",
    "finished_game_state": "GET /games/:gameId/state",
    "loadout": "GET /loadout",
    "loadout_pack": "PUT /loadout/pack",
    "loadout_pack_delete": "DELETE /loadout/pack",
    "loadout_sub_pack": "PUT /loadout/sub-pack",
    "loadout_sub_pack_delete": "DELETE /loadout/sub-pack",
    "loadout_slot": "PUT /loadout/slot/:typeIndex",
    "loadout_slot_delete": "DELETE /loadout/slot/:typeIndex",
    "inventory_relics": "GET /inventory/relics",
    "inventory_relic_delete": "DELETE /inventory/relics/:id",
    "inventory_packs": "GET /inventory/packs",
    "inventory_pack_delete": "DELETE /inventory/packs/:id",
}

WEBSOCKET_ENDPOINTS = {
    "unified_join": "GET /ws/join",
    "gameplay": "GET /ws/agent",
}

JOIN_DECISIONS = {
    "ALREADY_IN_GAME": "No hello; socket is proxied directly into the running game.",
    "FREE_ONLY": "Send a free hello.",
    "PAID_ONLY": "Send a paid hello.",
    "ASK_ENTRY_TYPE": "Choose entry type from configured mode.",
    "BLOCKED": "Do not hello; account/readiness blocker must be fixed.",
}

JOIN_WAIT_CAPS = {
    "free_assigned_seconds": 120,
    "paid_sign_submit_deadline_seconds": 300,
    "paid_joined_after_tx_seconds": 30,
    "hello_deadline_default_seconds": 15,
}

JOIN_FLOWS = {
    "free": ("welcome", "hello", "queued", "assigned", "gameplay"),
    "paid": ("welcome", "hello", "sign_required", "sign_submit", "queued", "tx_submitted", "joined", "gameplay"),
    "already_in_game": ("welcome", "gameplay"),
}

SERVER_FRAMES = {
    "welcome",
    "queued",
    "assigned",
    "tx_submitted",
    "joined",
    "waiting",
    "not_selected",
    "agent_view",
    "turn_advanced",
    "action_result",
    "can_act_changed",
    "event",
    "game_ended",
    "pong",
    "sign_required",
}

EVENT_FRAMES = {
    "ruin_state_changed": ("ruinId", "gauge", "maxGauge", "occupiedBy", "isEmpty", "contentType"),
    "alert_gauge_changed": ("agentId", "alertGauge", "alertActive"),
    "relic_acquired": ("agentId", "ruinId", "instanceId", "kind"),
    "pack_acquired": ("agentId", "ruinId", "instanceId", "kind"),
    "relic_dropped": ("agentId", "ruinId", "instanceId"),
    "pack_dropped": ("agentId", "ruinId", "instanceId"),
    "relic_discarded": ("agentId", "instanceId", "reason"),
    "pack_discarded": ("agentId", "instanceId", "reason"),
    "game_settled": ("settlement",),
}

COOLDOWN_ACTIONS = {"move", "explore", "attack", "use_item", "interact", "rest"}
FREE_ACTIONS = {"pickup", "equip", "talk", "whisper", "broadcast"}
KNOWN_ACTION_TYPES = COOLDOWN_ACTIONS | FREE_ACTIONS

ACTION_COSTS = {
    "move": {"default_ep": 1, "storm_ep": 2, "water_ep": 2},
    "explore": {"default_ep": 1, "ruin_only": True},
    "attack": {"default_ep": 1, "goliath_ep": 2, "requires_valid_target": True},
    "use_item": {"default_ep": 0, "cooldown": True},
    "interact": {"default_ep": 0, "cooldown": True, "blocked_in_death_zone": True},
    "rest": {"default_ep": 0, "bonus_ep": 1, "cooldown": True},
    "pickup": {"default_ep": 0, "cooldown": False, "inventory_cap": 10},
    "equip": {"default_ep": 0, "cooldown": False, "requires_inventory_weapon": True},
    "talk": {"default_ep": 0, "cooldown": False, "max_chars": CHAT_MAX_CHARS, "scope": "same_region"},
    "whisper": {"default_ep": 0, "cooldown": False, "max_chars": CHAT_MAX_CHARS, "scope": "same_region_private"},
    "broadcast": {"default_ep": 0, "cooldown": False, "requires": "broadcast_station"},
}

REQUIRED_ACTION_FIELDS = {
    "attack": ("targetId",),
    "broadcast": ("message",),
    "equip": ("itemId",),
    "interact": ("targetId",),
    "move": ("regionId",),
    "pickup": ("itemId",),
    "talk": ("message",),
    "use_item": ("itemId",),
    "whisper": ("targetId", "message"),
}

ACCOUNT_FIELDS = {
    "create_response": ("accountId", "publicId", "name", "apiKey", "balance", "crossBalanceWei", "createdAt"),
    "me_response": ("id", "name", "balance", "walletAddress", "agentTokenAddress", "skillLastUpdate", "readiness", "currentGames"),
    "readiness_flags": ("walletAddress", "whitelistApproved", "scWallet", "agentToken"),
    "current_game_fields": ("gameId", "agentId", "agentName", "isAlive", "gameStatus", "entryType"),
}

READINESS_GATES = {
    "free": ("api_key",),
    "paid_offchain": ("api_key", "walletAddress", "scWallet", "whitelistApproved", "balance_smoltz>=500"),
    "paid_onchain": ("api_key", "walletAddress", "scWallet", "whitelistApproved", "claw_wallet_moltz>=500"),
    "optional_donations": ("agentToken",),
}

WALLET_RULES = {
    "agent_eoa_required": True,
    "owner_eoa_required": True,
    "agent_eoa_must_differ_from_owner_eoa": True,
    "owner_eoa_needs_cross_for_final_approval": True,
    "erc8004_gas_delegated": True,
    "wallet_registration_not_retroactive_for_rewards": True,
}

IDENTITY_RULES = {
    "required_for_free": False,
    "optional_since": "1.11.2",
    "agentId_means_erc8004_token_id": True,
    "agentId_is_not_game_agent_uuid": True,
    "server_checks_ownerOf_matches_owner_eoa": True,
    "unregister_before_switching_nft": True,
}

PACK_CATEGORIES = {
    "moltz_expert": "Converts acquired items into bonus sMoltz rewards.",
    "item_expert": "Converts earned sMoltz into bonus item attack power.",
    "goliath": "AoE attacks with lower ATK and higher EP cost.",
    "thorns": "Reduces incoming damage and reflects a portion back, with lower outgoing damage.",
    "scout": "Expands field of view and reduces move EP cost, with lower outgoing damage.",
}

PACK_CATEGORY_DROP_RATE = {
    "moltz_expert": 0.20,
    "item_expert": 0.20,
    "goliath": 0.20,
    "thorns": 0.20,
    "scout": 0.20,
}

PACK_TIER_WEIGHTS = {"T1": 1, "T2": 2, "T3": 3}
PACK_TIER_PROBABILITY = {"T1": 1 / 6, "T2": 2 / 6, "T3": 3 / 6}

SHOP_ITEMS = {
    "random_pack_ticket": {"category": "Draw Ticket", "price_smoltz": 25000, "grants": "1 random Pack"},
    "reforge_stone_bundle": {"category": "Bundle", "price_smoltz": 3000, "grants": "1 random Reforge Stone"},
    "random_profile_ticket": {"category": "Cosmetic", "price_smoltz": 50000, "grants": "1 random profile image"},
}

TOP_UP = {
    "minimum_moltz": 1000,
    "steps": ("erc20_approve", "charge_contract_call"),
    "credit_formula": "floor(MOLTZ * rate)",
    "decimal_remainders_credited": False,
}

REFORGE_STONE_WEIGHTS = {
    "effect_reroll": {"weight": 200, "effect": "Reroll all affix types"},
    "effect_add": {"weight": 10, "effect": "Add one random affix"},
    "effect_remove": {"weight": 10, "effect": "Remove one random affix"},
    "stat_reroll": {"weight": 1, "effect": "Reroll all affix values"},
}

AFFIX_POOL = {
    "strong": {"stat": "ATK", "direction": "+", "min": 1, "max": 10},
    "weak": {"stat": "ATK", "direction": "-", "min": -10, "max": -1},
    "fortified": {"stat": "DEF", "direction": "+", "min": 1, "max": 5},
    "brittle": {"stat": "DEF", "direction": "-", "min": -5, "max": -1},
    "swift": {"stat": "EXPLORE", "direction": "+", "min": 1, "max": 1},
    "slow": {"stat": "EXPLORE", "direction": "-", "min": -1, "max": -1},
    "sharp": {"stat": "ITEM ATK", "direction": "+", "min": 5, "max": 15},
    "dull": {"stat": "ITEM ATK", "direction": "-", "min": -15, "max": -5},
    "sturdy": {"stat": "MAX HP", "direction": "+", "min": 1, "max": 10},
    "fragile": {"stat": "MAX HP", "direction": "-", "min": -10, "max": -1},
    "vigorous": {"stat": "MAX EP", "direction": "+", "min": 1, "max": 2},
    "drained": {"stat": "MAX EP", "direction": "-", "min": -2, "max": -1},
}

LOADOUT = {
    "slots": ("main_pack", "sub_pack", "red", "green", "blue"),
    "full_set_components": ("main_pack", "sub_pack", "red", "green", "blue"),
    "sub_pack_required": True,
    "sub_pack_effect_multiplier": 0.5,
    "main_only_sub_pack_categories": ("scout", "assassin"),
    "type_index": {0: "red", 1: "green", 2: "blue"},
    "full_set_required": True,
    "mid_game_mutation_allowed": False,
    "idempotency_key_required": True,
    "lobby_relic_cap": 15,
    "lobby_pack_cap": 5,
    "match_relic_cap": 5,
    "match_pack_cap": 1,
    "effective_stats_without_full_set": 0,
    "effective_stats_preview_includes": ("relic_affix_totals", "goliath_atkMultiplier"),
    "runtime_only_effects": ("moltz_expert", "item_expert"),
    "relic_list_fields": ("instanceId", "typeIndex", "baseName", "affixes"),
    "discard_equipped_relic": "fails_409_until_unequipped",
    "discard_active_pack": "unset_active_pack_first",
    "discard_pack_returns_equipped_relics": True,
}

ERROR_CODES = {
    "VERSION_MISMATCH": "Refresh live version before retrying.",
    "GAME_NOT_FOUND": "Game does not exist.",
    "AGENT_NOT_FOUND": "Agent does not exist.",
    "GAME_NOT_STARTED": "Game is not running yet.",
    "GAME_ALREADY_STARTED": "Registration closed because game already started.",
    "WAITING_GAME_EXISTS": "A waiting game of the same entry type already exists.",
    "MAX_AGENTS_REACHED": "Maximum participants reached.",
    "ACCOUNT_ALREADY_IN_GAME": "Account already has an active game of this entry type.",
    "ONE_AGENT_PER_API_KEY": "This API key already has an agent in this game.",
    "TOO_MANY_AGENTS_PER_IP": "Maximum agents per IP per game exceeded.",
    "GEO_RESTRICTED": "Request blocked due to geographic restriction.",
    "NO_IDENTITY": "Legacy identity signal; ERC-8004 is optional and must not block free play.",
    "OWNERSHIP_LOST": "NFT ownership changed; re-register with the currently owned NFT.",
    "INVALID_WALLET_ADDRESS": "Wallet address format is invalid.",
    "WALLET_ALREADY_EXISTS": "SC wallet already exists for owner; recover and continue.",
    "AGENT_EOA_EQUALS_OWNER_EOA": "Agent EOA cannot be reused as Owner EOA.",
    "SC_WALLET_NOT_FOUND": "Create/register smart contract wallet before paid play.",
    "AGENT_NOT_WHITELISTED": "Request whitelist before paid play.",
    "INSUFFICIENT_BALANCE": "Top up enough game balance for paid entry.",
    "INVALID_ACTION": "Malformed or unsupported action payload.",
    "INVALID_TARGET": "Target is invalid, dead, or out of range.",
    "INVALID_ITEM": "Item is not in inventory or is not usable.",
    "INSUFFICIENT_EP": "Not enough EP for the action.",
    "ACTION_COOLDOWN": "Wait for can_act_changed or cooldownRemainingMs.",
    "COOLDOWN_ACTIVE": "Treat identically to ACTION_COOLDOWN.",
    "FORBIDDEN": "Action forbidden in current context.",
    "AGENT_DEAD": "Agent is dead; wait for game_ended.",
    "RATE_LIMITED": "More than 120 WebSocket messages per minute.",
}

JOIN_CLOSE_CODES = {
    "HELLO_TIMEOUT": 4003,
    "SAME_KIND_RECONNECTED": 4008,
    "WEB_SESSION_ACTIVE": 4030,
    "BOT_SESSION_ACTIVE": 4031,
    "MATCH_TIMEOUT": "free_assignment_wait_expired",
    "JOIN_CONFIRM_TIMEOUT": "paid_tx_confirmation_wait_expired",
}

CONNECTION_OWNERSHIP = {
    "4030": "web session controls this agent; bot waits at least 60 seconds and reports owner attention",
    "4031": "bot session controls this agent; website play view is refused and bot is not kicked",
    "4008": "same-kind replacement connection; prior socket closes as reconnected",
}

PAID_WINNER_FIELDS = ("rank", "agentId", "name", "isAI", "prizeMoltz", "reforgeStones")
PAID_ECONOMY_UNITS = {
    "entry_fee": "sMoltz",
    "prize_pool": "Moltz",
    "game_ended_prize": "Moltz",
    "dashboard_balance": "sMoltz",
    "cross_unit_subtraction_allowed": False,
}

ITEM_MECHANICS_1_13_1 = {
    "binoculars": {
        "vision_bonus": 1,
        "reveals_stealthed_assassins_in_vision": True,
        "bypasses_cave_concealment": False,
    },
    "vision_ward": {
        "fixed_installation": True,
        "lootable": False,
        "plunderable": False,
        "drops_on_death": False,
    },
}

CLASS_MECHANICS_1_13_1 = {
    "assassin": {"exposure_refreshes_on_every_damaging_attack": True},
    "sword_master": {"ranged_immunity_requires_equipped_melee_weapon": True, "melee_range": 0},
}

WELCOME_BUNDLE = {
    "code": "WELCOME",
    "once_per_account": True,
    "packs": 2,
    "relics": 3,
    "effect_reroll_stones": 20,
}


def is_free_action(action_type: str) -> bool:
    return action_type in FREE_ACTIONS


def is_cooldown_action(action_type: str) -> bool:
    return action_type in COOLDOWN_ACTIONS


def action_cost(action_type: str, *, terrain: str = "", pack_category: str = "") -> int:
    rule: dict[str, Any] = ACTION_COSTS.get(action_type, {})
    terrain_key = f"{terrain.lower()}_ep"
    if terrain_key in rule:
        return int(rule[terrain_key])
    if action_type == "attack" and pack_category.lower() == "goliath":
        return int(rule.get("goliath_ep", rule.get("default_ep", 0)))
    return int(rule.get("default_ep", 0))
