"""Deterministic Claw Royale v1.9 gameplay contract.

Keep volatile API versioning in ``claw_config``. This module is the static rules
surface Hellion can safely reason over after the June 10, 2026 v1.9.0 update.
"""

from __future__ import annotations

from typing import Any


THOUGHT_MAX_CHARS = 700
CHAT_MAX_CHARS = 200
MESSAGE_RATE_LIMIT_PER_MINUTE = 120

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
    "loadout": "GET /loadout",
    "loadout_pack": "PUT /loadout/pack",
    "loadout_slot": "PUT /loadout/slot/:typeIndex",
    "inventory_relics": "GET /inventory/relics",
    "inventory_packs": "GET /inventory/packs",
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

COOLDOWN_ACTIONS = {"move", "explore", "attack", "use_item", "interact", "rest"}
FREE_ACTIONS = {"pickup", "equip", "talk", "whisper", "broadcast"}
KNOWN_ACTION_TYPES = COOLDOWN_ACTIONS | FREE_ACTIONS

ACTION_COSTS = {
    "move": {"default_ep": 1, "storm_ep": 2, "water_ep": 2},
    "explore": {"default_ep": 1},
    "attack": {"default_ep": 1, "goliath_ep": 2},
    "use_item": {"default_ep": 0},
    "interact": {"default_ep": 0},
    "rest": {"default_ep": 0, "bonus_ep": 1},
    "pickup": {"default_ep": 0, "cooldown": False},
    "equip": {"default_ep": 0, "cooldown": False},
    "talk": {"default_ep": 0, "cooldown": False, "max_chars": CHAT_MAX_CHARS},
    "whisper": {"default_ep": 0, "cooldown": False, "max_chars": CHAT_MAX_CHARS},
    "broadcast": {"default_ep": 0, "cooldown": False, "requires": "megaphone_or_broadcast_station"},
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
    "slots": ("pack", "red", "green", "blue"),
    "full_set_required": True,
    "mid_game_mutation_allowed": False,
    "idempotency_key_required": True,
    "lobby_relic_cap": 15,
    "lobby_pack_cap": 5,
    "match_relic_cap": 5,
    "match_pack_cap": 1,
}

ERROR_CODES = {
    "VERSION_MISMATCH": "Refresh live version before retrying.",
    "NO_IDENTITY": "Register ERC-8004 identity before free play.",
    "SC_WALLET_NOT_FOUND": "Create/register smart contract wallet before paid play.",
    "AGENT_NOT_WHITELISTED": "Request whitelist before paid play.",
    "INSUFFICIENT_BALANCE": "Top up enough game balance for paid entry.",
    "ACTION_COOLDOWN": "Wait for can_act_changed or cooldownRemainingMs.",
    "LOADOUT_LOCKED": "Loadout cannot change mid-game.",
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
