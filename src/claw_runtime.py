"""Always-on Claw Royale gameplay connector for Render.

The dashboard and /tick endpoint are only the shell. This worker owns the live
WebSocket loop: join/listen, pass snapshots through the deterministic cortex,
send actions back, and persist compact runtime status for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import websockets

from autonomy_suggestions import record_autonomy_observation
from claw_contract import JOIN_DECISIONS, THOUGHT_MAX_CHARS
from claw_config import CLAW_API_BASE, active_claw_version, claw_api_base, reconcile_claw_version
from claw_signing import ClawSigningError, sign_typed_data_frame
from core_loop import cerberus_tick
from env_loader import hydrate_env
from memory_system import CompactMemoryStore
from onboarding_clients import ClawRoyaleClient
from runtime_state import (
    claw_runtime_status_file,
    clear_game_id,
    read_json,
    remember_game_id,
    stored_game_id,
    update_claw_runtime_status,
    write_json,
)


DEFAULT_MIN_RECONNECT_SECONDS = 5
DEFAULT_MAX_RECONNECT_SECONDS = 90
DEFAULT_ROUTE_PROBE_SECONDS = 15
DEFAULT_PAID_RETRY_COOLDOWN_SECONDS = 600
JOIN_PATH = "/ws/join"
AGENT_PATH = "/ws/agent"
NO_HELLO_DECISIONS = {
    key for key, value in JOIN_DECISIONS.items() if "No hello" in value or "Do not hello" in value
}
DEFAULT_WS_PATHS = (
    "/ws/join",
)
VALID_GAME_MODES = {"free", "offchain", "onchain"}
ROOM_LIST_KEYS = (
    "rooms",
    "games",
    "availableRooms",
    "availableGames",
    "waitingRooms",
    "waitingGames",
    "lobbies",
    "matches",
)
from turn_state_model import TurnState
ROOM_COUNT_KEYS = (
    "agentCount",
    "agentsCount",
    "playerCount",
    "playersCount",
    "participantCount",
    "participantsCount",
    "currentAgents",
    "currentPlayers",
    "occupancy",
    "population",
)
ROOM_LIST_COUNT_KEYS = ("agents", "players", "participants", "joinedAgents", "activeAgents")


@dataclass(frozen=True)
class ClawRuntimeConfig:
    api_key: str
    api_base: str = CLAW_API_BASE
    version: str = ""
    mode: str = "paid"
    enabled: bool = False
    min_reconnect_seconds: int = DEFAULT_MIN_RECONNECT_SECONDS
    max_reconnect_seconds: int = DEFAULT_MAX_RECONNECT_SECONDS

    @property
    def base_url(self) -> str:
        return self.api_base.rstrip("/")

    @property
    def ws_base_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}"

    @property
    def headers(self) -> dict[str, str]:
        headers = {"X-API-Key": self.api_key}
        if self.version:
            headers["X-Version"] = self.version
        return headers


def update_status(**updates: Any) -> None:
    update_claw_runtime_status(**updates)


def extract_game_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = next(
        (payload.get(key) for key in ("data", "payload", "state", "agentView") if isinstance(payload.get(key), dict)),
        payload,
    )
    view = data.get("view", {}) if isinstance(data, dict) else {}
    current_game = view.get("currentGame", {}) if isinstance(view, dict) else {}
    game = data.get("game", {}) if isinstance(data, dict) else {}
    candidates = [
        payload.get("gameId"),
        payload.get("game_id"),
        data.get("gameId") if isinstance(data, dict) else "",
        data.get("game_id") if isinstance(data, dict) else "",
        game.get("id") if isinstance(game, dict) else "",
        game.get("gameId") if isinstance(game, dict) else "",
        current_game.get("id") if isinstance(current_game, dict) else "",
        view.get("gameId") if isinstance(view, dict) else "",
    ]
    return next((str(item) for item in candidates if item), "")


def frame_value(payload: dict[str, Any], key: str) -> Any:
    if key in payload:
        return payload.get(key)
    data = payload.get("data")
    if isinstance(data, dict):
        return data.get(key)
    return ""


def unwrap_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    data = next(
        (payload.get(key) for key in ("data", "payload", "state", "agentView") if isinstance(payload.get(key), dict)),
        payload,
    )
    if frame_type in {"agent_view", "turn_advanced", "joined", "state", "snapshot"}:
        return data if isinstance(data, dict) else None
    if isinstance(data, dict) and ("view" in data or "gameId" in data or "game_id" in data):
        return data
    return None


def game_status(payload: dict[str, Any], snapshot: dict[str, Any] | None = None) -> str:
    candidates: list[Any] = []
    for item in (payload, snapshot or {}):
        if not isinstance(item, dict):
            continue
        data = next(
            (item.get(key) for key in ("data", "payload", "state", "agentView") if isinstance(item.get(key), dict)),
            item,
        )
        view = data.get("view", {}) if isinstance(data, dict) else {}
        game = data.get("game", {}) if isinstance(data, dict) else {}
        current_game = view.get("currentGame", {}) if isinstance(view, dict) else {}
        candidates.extend(
            [
                item.get("status"),
                item.get("gameStatus"),
                data.get("status") if isinstance(data, dict) else "",
                data.get("gameStatus") if isinstance(data, dict) else "",
                game.get("status") if isinstance(game, dict) else "",
                game.get("gameStatus") if isinstance(game, dict) else "",
                view.get("status") if isinstance(view, dict) else "",
                view.get("gameStatus") if isinstance(view, dict) else "",
                current_game.get("status") if isinstance(current_game, dict) else "",
                current_game.get("gameStatus") if isinstance(current_game, dict) else "",
            ]
        )
    return next((str(item).strip().lower() for item in candidates if item), "")


def is_running_game_status(status: str) -> bool:
    return status.lower() in {"running", "active", "started", "in_progress"}


def is_non_running_game_status(status: str) -> bool:
    return status.lower() in {"waiting", "queued", "assigned", "joined", "created", "pending", "not_started", "lobby"}


def is_terminal_game_error(message: str) -> bool:
    lowered = message.lower()
    return any(
        marker in lowered
        for marker in (
            "game_already_over",
            "already ended",
            "game ended",
            "game is over",
            "game_over",
            "agent_dead",
            "agent is dead",
        )
    )


def action_envelope(action: dict[str, Any]) -> dict[str, Any]:
    thought = public_action_thought(action)
    data = {key: value for key, value in action.items() if not key.startswith("_")}
    return {"type": "action", "data": data, "thought": thought}


def public_action_thought(action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "").lower()
    reason = str(action.get("reason") or action.get("thought") or "").lower()
    lines = {
        "pickup": "Hellion notices tribute on the floor and graciously accepts.",
        "equip": "Hellion upgrades the problem. Everyone else may update their wills.",
        "move": "Hellion relocates with intent. The map is learning manners.",
        "explore": "Hellion opens another door because uncertainty was looking too comfortable.",
        "attack": "Hellion applies peer review. Results may be terminal.",
        "use_item": "Hellion chooses continuity. Dramatic, practical, irritatingly alive.",
        "interact": "Hellion presses the useful button. Civilization trembles.",
        "talk": str(action.get("message") or "Hellion speaks, and the arena pretends it was ready."),
        "whisper": "Hellion lowers her voice. Somehow that makes it worse.",
        "broadcast": str(action.get("message") or "Hellion would like the arena to know this was avoidable."),
        "rest": "Hellion pauses only because even nightmares respect cooldowns.",
    }
    if "death-zone" in reason or "death zone" in reason:
        return "Hellion refuses to be seasoned by the arena. Moving."
    if "weapon" in reason or "upgrade" in reason:
        return "Hellion found sharper punctuation."
    if "scout fallback" in reason:
        return "Hellion scouts forward. Standing still is for statues."
    return lines.get(action_type, "Hellion proceeds. The arena may file complaints.")[:THOUGHT_MAX_CHARS]


def snapshot_summary(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {}
    state = TurnState.from_snapshot(snapshot)
    return {
        "game_id": state.game_id,
        "agent_id": state.agent_id,
        "turn": state.turn,
        "status": state.status,
        "hp": state.self.hp,
        "max_hp": state.self.max_hp,
        "ep": state.self.ep,
        "max_ep": state.self.max_ep,
        "atk": state.self.atk,
        "defense": state.self.defense,
        "alive": state.self.is_alive,
        "region_id": state.current_region.id,
        "region_name": state.current_region.name,
        "terrain": state.current_region.terrain,
        "death_zone": state.current_region.is_death_zone,
        "visible_agents": len(state.visible_agents),
        "visible_monsters": len(state.visible_monsters),
        "visible_items": len(state.visible_items) + len(state.current_region.items),
        "inventory_count": len(state.inventory),
        "alert_gauge": state.alert_gauge,
        "can_act": state.can_take_main_action,
    }


def sign_submit_frame(signed_frame: dict[str, Any]) -> dict[str, Any]:
    frame = {"type": "sign_submit", "signature": signed_frame["signature"]}
    if signed_frame.get("joinIntentId"):
        frame["joinIntentId"] = signed_frame["joinIntentId"]
    elif signed_frame.get("requestId"):
        frame["requestId"] = signed_frame["requestId"]
    return frame


def room_entries(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    entries: list[dict[str, Any]] = []
    containers = [payload]
    for key in ("data", "payload", "state", "join", "lobby"):
        value = payload.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in ROOM_LIST_KEYS:
            value = container.get(key)
            if isinstance(value, list):
                entries.extend(item for item in value if isinstance(item, dict))
            elif isinstance(value, dict):
                for nested in value.values():
                    if isinstance(nested, list):
                        entries.extend(item for item in nested if isinstance(item, dict))
                    elif isinstance(nested, dict):
                        entries.append(nested)
    return entries


def room_entry_type(room: dict[str, Any]) -> str:
    for key in ("entryType", "entry_type", "entry", "type", "roomType", "gameType", "tier", "mode"):
        value = str(room.get(key) or "").strip().lower()
        if value in {"free", "public"}:
            return "free"
        if value in {"paid", "premium", "offchain", "onchain"}:
            return "paid"
    for key in ("isPremium", "premium", "paid", "isPaid"):
        value = room.get(key)
        if isinstance(value, bool):
            return "paid" if value else "free"
    return ""


def room_population(room: dict[str, Any]) -> int | None:
    for key in ROOM_LIST_COUNT_KEYS:
        value = room.get(key)
        if isinstance(value, list):
            return len(value)
    for key in ROOM_COUNT_KEYS:
        value = room.get(key)
        if isinstance(value, dict):
            nested = room_population(value)
            if nested is not None:
                return nested
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return None


def room_choice_summary(welcome: dict[str, Any] | None) -> dict[str, Any]:
    paid_populations: list[int] = []
    free_populations: list[int] = []
    unknown_rooms = 0
    for room in room_entries(welcome):
        entry_type = room_entry_type(room)
        population = room_population(room)
        if entry_type == "paid" and population is not None:
            paid_populations.append(population)
        elif entry_type == "free" and population is not None:
            free_populations.append(population)
        elif entry_type:
            unknown_rooms += 1
    return {
        "paid_rooms": len(paid_populations),
        "paid_occupied": sum(1 for count in paid_populations if count > 0),
        "free_rooms": len(free_populations),
        "free_occupied": sum(1 for count in free_populations if count > 0),
        "unknown_rooms": unknown_rooms,
    }


def readiness_blocks_free(welcome: dict[str, Any] | None) -> bool:
    readiness = (welcome or {}).get("readiness", {})
    if not isinstance(readiness, dict):
        return False
    for key in ("free", "freeReady", "free_ready", "erc8004_identity", "erc8004Identity", "identityReady"):
        if readiness.get(key) is False:
            return True
    free_room = readiness.get("freeRoom") or readiness.get("free_room")
    if isinstance(free_room, dict):
        if free_room.get("ok") is False:
            return True
        missing = free_room.get("missing") or free_room.get("blockers") or free_room.get("errors")
        if isinstance(missing, list):
            return any("identity" in str(item).lower() or "no_identity" in str(item).lower() for item in missing)
    errors = readiness.get("errors") or readiness.get("blockers") or readiness.get("missing")
    if isinstance(errors, list):
        return any("identity" in str(item).lower() or "no_identity" in str(item).lower() for item in errors)
    return False


def free_fallback_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_FREE_FALLBACK_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}


def should_prefer_free_room(config: ClawRuntimeConfig, welcome: dict[str, Any] | None) -> bool:
    if not free_fallback_enabled() or config.mode == "free" or readiness_blocks_free(welcome):
        return False
    summary = room_choice_summary(welcome)
    if summary["free_occupied"] <= 0:
        return False
    if summary["paid_rooms"] == 0 and summary["unknown_rooms"] == 0:
        return True
    return summary["paid_rooms"] > 0 and summary["paid_occupied"] == 0


def _readiness_paid_mode(welcome: dict[str, Any] | None) -> str:
    readiness = (welcome or {}).get("readiness", {})
    if not isinstance(readiness, dict):
        return ""
    paid_room = readiness.get("paidRoom") or readiness.get("paid_room") or readiness.get("paid")
    if not isinstance(paid_room, dict) or paid_room.get("ok") is False:
        return ""
    modes = paid_room.get("mode") if isinstance(paid_room.get("mode"), dict) else {}
    if modes.get("onchain") is True:
        return "onchain"
    if modes.get("offchain") is True:
        return "offchain"
    return "onchain" if paid_room.get("onchain") is True else ("offchain" if paid_room.get("offchain") is True else "")


def should_auto_upgrade_to_paid(config: ClawRuntimeConfig, welcome: dict[str, Any] | None) -> str:
    """Prefer ready paid rooms even if Render was left in free mode.

    The env var is a default posture, not a profit suicide pact. If Claw's
    welcome readiness explicitly says paid on-chain/off-chain is available, use
    it so a stale Render env cannot keep Hellion grinding free rooms forever.
    """

    if config.mode != "free":
        return ""
    if os.getenv("CLAW_ROYALE_DISABLE_PAID_AUTO_UPGRADE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return ""
    if recent_paid_join_failure_active():
        return ""
    return _readiness_paid_mode(welcome)


def recent_paid_join_failure_active() -> bool:
    status = read_json(claw_runtime_status_file())
    error = str(status.get("last_error") or "").lower()
    if "join onchain failed" not in error and "join offchain failed" not in error:
        return False
    try:
        cooldown = int(os.getenv("CLAW_ROYALE_PAID_RETRY_COOLDOWN_SECONDS", str(DEFAULT_PAID_RETRY_COOLDOWN_SECONDS)))
    except ValueError:
        cooldown = DEFAULT_PAID_RETRY_COOLDOWN_SECONDS
    if cooldown <= 0:
        return False
    try:
        updated_at = int(status.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0
    return updated_at > 0 and int(time.time()) - updated_at < cooldown


def account_paid_ready(account: dict[str, Any], *, minimum_smoltz: float = 500.0) -> bool:
    if not account.get("ok"):
        return False
    readiness = account.get("readiness") if isinstance(account.get("readiness"), dict) else {}
    if readiness.get("paidReady") is True:
        return True
    try:
        balance = float(account.get("balance") or 0)
    except (TypeError, ValueError):
        balance = 0.0
    return (
        bool(readiness.get("walletAddress"))
        and bool(readiness.get("scWallet"))
        and bool(readiness.get("whitelistApproved"))
        and balance >= minimum_smoltz
    )


def account_identity_ready(account: dict[str, Any]) -> bool:
    readiness = account.get("readiness") if isinstance(account.get("readiness"), dict) else {}
    return bool(readiness.get("identity") or readiness.get("erc8004_identity") or readiness.get("identityReady"))


def join_blocker_for_account(config: ClawRuntimeConfig, account: dict[str, Any]) -> str:
    if not account.get("ok"):
        return str(account.get("error") or "account status unavailable")
    paid_ready = account_paid_ready(account)
    identity_ready = account_identity_ready(account)
    if config.mode != "free" and not paid_ready and not identity_ready:
        return "paused before join: v2 needs ERC-8004 identity or at least 500 sMoltz paid balance"
    if config.mode == "free" and not identity_ready:
        return "paused before free join: v2 needs ERC-8004 identity"
    if config.mode != "free" and not paid_ready:
        return "paid join blocked: v2 needs at least 500 sMoltz balance or on-chain paid readiness"
    return ""


def hello_frame(config: ClawRuntimeConfig, welcome: dict[str, Any] | None = None) -> dict[str, Any] | None:
    decision = str((welcome or {}).get("decision") or "").upper()
    if decision in NO_HELLO_DECISIONS:
        return None
    paid_mode = should_auto_upgrade_to_paid(config, welcome)
    if paid_mode:
        return {"type": "hello", "entryType": "paid", "mode": paid_mode}
    if decision == "FREE_ONLY":
        return {"type": "hello", "entryType": "free"}
    if decision in {"", "ASK_ENTRY_TYPE"} and should_prefer_free_room(config, welcome):
        return {"type": "hello", "entryType": "free"}
    if decision == "PAID_ONLY":
        return {"type": "hello", "entryType": "paid", "mode": config.mode}
    if config.mode == "free":
        return {"type": "hello", "entryType": "free"}
    return {"type": "hello", "entryType": "paid", "mode": config.mode}


def normalize_game_mode(value: str) -> str:
    mode = value.strip().lower()
    if mode in {"paid", "paid_offchain", ""}:
        return "offchain"
    if mode in VALID_GAME_MODES:
        return mode
    return "offchain"


def wants_action(payload: dict[str, Any], snapshot: dict[str, Any] | None, *, gameplay_ready: bool = False) -> bool:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    status = game_status(payload, snapshot)
    if is_non_running_game_status(status):
        return False
    if not gameplay_ready and not is_running_game_status(status):
        return False
    if snapshot:
        state = TurnState.from_snapshot(snapshot)
        if not has_usable_turn_facts(state):
            return False
        view = snapshot.get("view", {}) if isinstance(snapshot.get("view"), dict) else {}
        if snapshot.get("canAct") is False:
            return False
        if view.get("canAct") is False:
            return False
    if frame_type in {"agent_view", "turn_advanced"}:
        return True
    if snapshot and snapshot.get("canAct") is True:
        return True
    view = snapshot.get("view", {}) if isinstance(snapshot, dict) else {}
    return isinstance(view, dict) and view.get("canAct") is True


def has_usable_turn_facts(state: TurnState) -> bool:
    return bool(
        state.self.id
        or state.current_region.id
        or state.connected_regions
        or state.visible_regions
        or state.visible_agents
        or state.visible_monsters
        or state.visible_items
        or state.current_region.items
        or state.inventory
    )


def discover_version(api_base: str = CLAW_API_BASE) -> str:
    configured = active_claw_version()
    try:
        version = reconcile_claw_version(api_base)
        update_status(configured_version=configured, live_version=version, version_reconciled=version != configured)
        return version
    except Exception as exc:
        update_status(last_error=f"version discovery failed: {str(exc)[:180]}")
        return os.getenv("CLAW_ROYALE_VERSION", "1.9.0").strip() or "1.9.0"


def load_config() -> ClawRuntimeConfig:
    hydrate_env(
        (
            "CLAW_ROYALE_API_KEY",
            "CLAW_ROYALE_VERSION",
            "CLAW_ROYALE_RUNTIME_ENABLED",
            "CLAW_ROYALE_GAME_MODE",
        )
    )
    api_key = os.getenv("CLAW_ROYALE_API_KEY", "").strip()
    enabled_raw = os.getenv("CLAW_ROYALE_RUNTIME_ENABLED", "").strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"}
    mode = normalize_game_mode(os.getenv("CLAW_ROYALE_GAME_MODE", "offchain"))
    base = claw_api_base()
    version = discover_version(base)
    return ClawRuntimeConfig(api_key=api_key, api_base=base, version=version, mode=mode, enabled=enabled)


def websocket_paths() -> list[str]:
    raw = os.getenv("CLAW_ROYALE_WS_PATHS", "").strip()
    if not raw:
        single = os.getenv("CLAW_ROYALE_WS_PATH", "").strip()
        if single:
            raw = single
    paths = [item.strip() for item in raw.split(",") if item.strip()] if raw else list(DEFAULT_WS_PATHS)
    normalized = []
    for path in paths:
        if path.startswith(("ws://", "wss://")):
            normalized.append(path)
        else:
            normalized.append(path if path.startswith("/") else f"/{path}")
    return list(dict.fromkeys(normalized))


def websocket_url(config: ClawRuntimeConfig, path: str) -> str:
    if path.startswith(("ws://", "wss://")):
        return path
    return f"{config.ws_base_url}{path}"


def reconnect_delay_seconds(config: ClawRuntimeConfig, reconnects: int, error: Exception) -> int:
    message = str(error).lower()
    if "http 404" in message or "not found" in message:
        raw = os.getenv("CLAW_ROYALE_ROUTE_PROBE_SECONDS", str(DEFAULT_ROUTE_PROBE_SECONDS))
        try:
            return max(3, min(config.max_reconnect_seconds, int(raw)))
        except ValueError:
            return DEFAULT_ROUTE_PROBE_SECONDS
    return min(config.max_reconnect_seconds, config.min_reconnect_seconds * reconnects)


def account_status_summary(config: ClawRuntimeConfig) -> dict[str, Any]:
    try:
        account = ClawRoyaleClient(api_key=config.api_key, base_url=config.base_url).me()
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
    readiness = account.get("readiness") if isinstance(account.get("readiness"), dict) else {}
    games = account.get("currentGames") if isinstance(account.get("currentGames"), list) else []
    return {
        "ok": True,
        "name": account.get("name", ""),
        "walletAddress": account.get("walletAddress", ""),
        "balance": account.get("balance", ""),
        "readiness": readiness,
        "currentGames": [
            {
                "gameId": game.get("gameId", ""),
                "agentId": game.get("agentId", ""),
                "gameStatus": game.get("gameStatus", ""),
                "entryType": game.get("entryType", ""),
                "isAlive": game.get("isAlive", ""),
            }
            for game in games
            if isinstance(game, dict)
        ],
    }


def _status_snapshot_to_compact_state(status: dict[str, Any]) -> dict[str, Any]:
    snapshot = status.get("last_snapshot") if isinstance(status.get("last_snapshot"), dict) else {}
    return {
        "gameId": snapshot.get("game_id") or status.get("current_game_id") or "",
        "turn": snapshot.get("turn") or 0,
        "view": {
            "self": {
                "id": snapshot.get("agent_id") or status.get("agent_id") or "",
                "hp": snapshot.get("hp") or 0,
                "maxHp": snapshot.get("max_hp") or 100,
                "ep": snapshot.get("ep") or 0,
                "maxEp": snapshot.get("max_ep") or 10,
                "atk": snapshot.get("atk") or 0,
                "def": snapshot.get("defense") or 0,
                "isAlive": snapshot.get("alive", True),
            },
            "currentRegion": {
                "id": snapshot.get("region_id") or "",
                "name": snapshot.get("region_name") or "",
                "terrain": snapshot.get("terrain") or "",
                "isDeathZone": snapshot.get("death_zone", False),
            },
            "alertGauge": snapshot.get("alert_gauge") or 0,
        },
    }


def record_action_result_learning(payload: dict[str, Any], *, status: dict[str, Any] | None = None) -> None:
    status = status if isinstance(status, dict) else read_json(claw_runtime_status_file())
    last_action = status.get("last_action") if isinstance(status.get("last_action"), dict) else {}
    if not last_action:
        return
    snapshot = status.get("last_snapshot") if isinstance(status.get("last_snapshot"), dict) else {}

    error_text = str(payload.get("message") or payload.get("error") or "")
    success_raw = payload.get("success", payload.get("ok"))
    accepted = not error_text and success_raw is not False
    code = payload.get("code") or payload.get("status") or payload.get("errorCode") or ""
    action_type = str(last_action.get("type") or "unknown")
    outcome = {
        "ok": accepted,
        "code": code or ("accepted" if accepted else "rejected"),
        "message": error_text,
        "error": error_text,
    }

    store = CompactMemoryStore().load()
    store.remember_turn(
        _status_snapshot_to_compact_state(status),
        action=last_action,
        outcome={
            **outcome,
            "hp": payload.get("hp") or payload.get("currentHp") or snapshot.get("hp"),
            "ep": payload.get("ep") or payload.get("currentEp") or snapshot.get("ep"),
        },
    )
    if accepted:
        store.remember_lesson(
            "runtime",
            f"action_result: {action_type} accepted; keep this route available when the same board shape returns",
            source="frame:action_result",
            confidence="0.79",
        )
    else:
        detail = error_text or str(code or "unknown_error")
        store.remember_lesson(
            "runtime",
            f"action_result: {action_type} failed with {detail[:120]}; avoid repeating that request blindly",
            source="frame:action_result",
            confidence="0.9",
        )
    store.save()
    try:
        record_autonomy_observation(_status_snapshot_to_compact_state(status), last_action, outcome=outcome, runtime=status)
    except Exception:
        return


def _balance_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def record_account_balance(config: ClawRuntimeConfig, *, stage: str) -> dict[str, Any]:
    status = read_json(claw_runtime_status_file())
    account = account_status_summary(config)
    if not account.get("ok"):
        update_status(account=account, balance_stage=stage)
        return account

    balance = _balance_float(account.get("balance"))
    first_balance = status.get("first_balance")
    last_balance = status.get("last_balance")
    if first_balance in (None, ""):
        first_balance = balance
    delta = balance - _balance_float(last_balance if last_balance not in (None, "") else first_balance)
    total_delta = balance - _balance_float(first_balance)
    completed = int(status.get("games_completed") or 0)
    if stage == "game_ended":
        completed += 1
    per_game = total_delta / completed if completed > 0 else 0.0
    update_status(
        account=account,
        balance_stage=stage,
        first_balance=first_balance,
        last_balance=balance,
        last_balance_delta=round(delta, 6),
        total_balance_delta=round(total_delta, 6),
        games_completed=completed,
        average_balance_delta_per_game=round(per_game, 6),
        games_needed_for_1000_per_day=int((1000 + per_game - 1) // per_game) if per_game > 0 else 0,
    )
    return account


async def connect_and_play(config: ClawRuntimeConfig, path: str) -> None:
    version = discover_version(config.base_url)
    if version != config.version:
        config = ClawRuntimeConfig(
            api_key=config.api_key,
            api_base=config.api_base,
            version=version,
            mode=config.mode,
            enabled=config.enabled,
            min_reconnect_seconds=config.min_reconnect_seconds,
            max_reconnect_seconds=config.max_reconnect_seconds,
        )
    url = websocket_url(config, path)
    extra_headers = config.headers
    account_status = record_account_balance(config, stage="connect")
    join_blocker = join_blocker_for_account(config, account_status)
    update_status(state="connecting", endpoint=url, version=config.version, mode=config.mode, account=account_status, last_error="")
    async with websockets.connect(url, additional_headers=extra_headers, ping_interval=20, ping_timeout=20) as ws:
        gameplay_ready = False
        update_status(state="connected", endpoint=url, connected_at=int(time.time()), reconnects=read_json(claw_runtime_status_file()).get("reconnects", 0))
        async for raw in ws:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
            except Exception:
                update_status(last_error="received non-json websocket frame")
                continue
            if not isinstance(payload, dict):
                continue
            frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "message")
            if frame_type in {"ping"}:
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if frame_type == "welcome":
                frame = None if join_blocker else hello_frame(config, payload)
                room_summary = room_choice_summary(payload)
                update_status(
                    state="welcomed",
                    last_frame_type=frame_type,
                    join_decision=payload.get("decision", ""),
                    join_readiness=payload.get("readiness", {}),
                    join_room_choice=frame.get("entryType", "") if frame else "",
                    join_room_choice_reason=(
                        "occupied_free_room_over_empty_paid_rooms"
                        if frame and frame.get("entryType") == "free" and should_prefer_free_room(config, payload)
                        else "server_or_config_default"
                    ),
                    join_room_summary=room_summary,
                    last_error=join_blocker or ("" if frame else str(payload.get("instruction") or "")),
                )
                if frame:
                    await ws.send(json.dumps(frame, ensure_ascii=True, separators=(",", ":")))
                    update_status(state="hello_sent", last_hello=frame)
                continue
            if frame_type in {"queued", "assigned", "tx_submitted", "joined", "waiting", "not_selected"}:
                gameplay_ready = False
                game_id = extract_game_id(payload)
                if game_id:
                    remember_game_id(game_id)
                update_status(
                    state=frame_type,
                    last_frame_type=frame_type,
                    current_game_id=game_id or stored_game_id(),
                    tx_hash=frame_value(payload, "txHash") or "",
                    last_error=str(payload.get("message") or payload.get("error") or ""),
                )
                continue
            if frame_type in {"action_result", "can_act_changed"}:
                error_text = str(payload.get("message") or payload.get("error") or "")
                if "not running" in error_text.lower():
                    gameplay_ready = False
                if frame_type == "action_result":
                    record_action_result_learning(payload)
                if is_terminal_game_error(error_text):
                    clear_game_id()
                    record_account_balance(config, stage="game_ended")
                    update_status(
                        state="game_ended",
                        last_frame_type=frame_type,
                        current_game_id="",
                        game_status="ended",
                        gameplay_ready=False,
                        can_act=False,
                        cooldown_remaining_ms=0,
                        last_error=error_text,
                    )
                    await ws.close(code=1000, reason="terminal game error")
                    return
                update_status(
                    state=frame_type,
                    last_frame_type=frame_type,
                    can_act=frame_value(payload, "canAct"),
                    cooldown_remaining_ms=frame_value(payload, "cooldownRemainingMs"),
                    last_error=error_text,
                )
                continue
            if frame_type in {"sign_required", "signature_required", "paid_join_signature_required"}:
                try:
                    signed_frame = sign_typed_data_frame(payload)
                    sign_submit = sign_submit_frame(signed_frame)
                    await ws.send(json.dumps(sign_submit, ensure_ascii=True, separators=(",", ":")))
                    update_status(
                        state="signed_paid_join",
                        last_frame_type=frame_type,
                        last_error="",
                        last_signature_at=int(time.time()),
                        last_sign_submit_keys=sorted(sign_submit.keys()),
                        signing_mode=signed_frame.get("signingMode", ""),
                        signer_address=signed_frame.get("signerAddress", ""),
                        signed_message_hash=signed_frame.get("messageHash", ""),
                        signed_message_length=signed_frame.get("messageLength", ""),
                        signed_message_preview=signed_frame.get("messagePreview", ""),
                    )
                except ClawSigningError as exc:
                    update_status(
                        state="blocked",
                        last_frame_type=frame_type,
                        last_error=str(exc)[:500],
                        last_payload_keys=sorted(payload.keys()),
                    )
                continue
            snapshot = unwrap_snapshot(payload)
            game_id = extract_game_id(payload) or extract_game_id(snapshot or {})
            if game_id:
                remember_game_id(game_id)
            status = game_status(payload, snapshot)
            if is_running_game_status(status):
                gameplay_ready = True
            elif is_non_running_game_status(status):
                gameplay_ready = False
            elif is_terminal_game_error(str(payload.get("message") or payload.get("error") or status)):
                clear_game_id()
                record_account_balance(config, stage="game_ended")
                update_status(
                    state="game_ended",
                    last_frame_type=frame_type,
                    current_game_id="",
                    game_status="ended",
                    gameplay_ready=False,
                    can_act=False,
                    last_error=str(payload.get("message") or payload.get("error") or status),
                )
                await ws.close(code=1000, reason="terminal game error")
                return
            update_status(
                last_frame_type=frame_type,
                current_game_id=game_id or stored_game_id(),
                game_status=status,
                gameplay_ready=gameplay_ready,
                last_snapshot=snapshot_summary(snapshot),
            )
            if snapshot and wants_action(payload, snapshot, gameplay_ready=gameplay_ready):
                action = cerberus_tick(snapshot)
                envelope = action_envelope(action)
                await ws.send(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
                update_status(last_action=action, last_action_at=int(time.time()), state="playing")
            elif snapshot and not gameplay_ready:
                update_status(state="waiting_for_running_game", last_error="joined but waiting for running game frame")
        update_status(state="socket_closed", last_error="websocket closed without terminal game frame")


async def run_forever(config: ClawRuntimeConfig | None = None) -> None:
    config = config or load_config()
    if not config.enabled:
        update_status(state="disabled", last_error="Set CLAW_ROYALE_RUNTIME_ENABLED=true to start the Claw runtime.")
        return
    if not config.api_key:
        update_status(state="blocked", last_error="Missing CLAW_ROYALE_API_KEY.")
        return
    reconnects = 0
    paths = websocket_paths()
    path_index = 0
    while True:
        path = paths[path_index % len(paths)]
        try:
            await connect_and_play(config, path)
        except Exception as exc:
            reconnects += 1
            path_index += 1
            delay = reconnect_delay_seconds(config, reconnects, exc)
            status = read_json(claw_runtime_status_file())
            status["reconnects"] = reconnects
            status["state"] = "reconnecting"
            status["last_error"] = str(exc)[:500]
            status["next_retry_seconds"] = delay
            status["last_failed_path"] = path
            status["next_path"] = paths[path_index % len(paths)]
            status["candidate_paths"] = paths
            status["updated_at"] = int(time.time())
            write_json(claw_runtime_status_file(), status)
            try:
                record_autonomy_observation(
                    _status_snapshot_to_compact_state(status),
                    {"type": "rest", "reason": "runtime reconnect after websocket exception"},
                    outcome={"ok": False, "code": "runtime_exception", "message": str(exc)[:500]},
                    runtime=status,
                )
            except Exception:
                pass
            await asyncio.sleep(delay)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
