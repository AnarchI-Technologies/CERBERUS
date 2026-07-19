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
from action_postmortem import build_action_postmortem
from claw_contract import COOLDOWN_ACTIONS, FREE_ACTIONS, JOIN_DECISIONS, THOUGHT_MAX_CHARS
from claw_policy_shadow import ENFORCED_FREE_ACTIONS, authorize_free_action_execution
from claw_config import CLAW_API_BASE, active_claw_version, claw_api_base, reconcile_claw_version
from claw_signing import ClawSigningError, sign_typed_data_frame
from core_loop import cerberus_tick
from env_loader import hydrate_env
from execution_coordinator import execute_authorized, reconcile_reserved_free_actions
from external_wisdom import shared_public_line
from free_action_abuse import FreeActionCortex
from game_map import build_live_map
from lesson_compiler import compile_lessons
from loadout_shop_reforge import (
    build_prejoin_plan,
    execute_loadout_operations,
    execute_reforge_candidates,
    execute_shop_recommendations,
)
from memory_system import CompactMemoryStore
from onboarding_clients import ClawRoyaleClient
from postgame_hardening import run_postgame_hardening_pass
from preseason1_claims import claim_reached_preseason1_points
from runtime_state import (
    append_action_postmortem,
    append_social_event,
    claw_runtime_status_file,
    clear_game_id,
    read_json,
    remember_stale_paid_room,
    remember_game_id,
    stale_paid_rooms,
    stored_game_id,
    update_claw_runtime_status,
    write_json,
)
DEFAULT_MIN_RECONNECT_SECONDS = 5
DEFAULT_MAX_RECONNECT_SECONDS = 90
DEFAULT_ROUTE_PROBE_SECONDS = 15
DEFAULT_PAID_RETRY_COOLDOWN_SECONDS = 600
DEFAULT_PRESEASON1_CLAIM_INTERVAL_SECONDS = 60
BACKGROUND_TASKS: set[asyncio.Task[Any]] = set()
JOIN_PATH = "/ws/join"
AGENT_PATH = "/ws/agent"
NO_HELLO_DECISIONS = {
    key for key, value in JOIN_DECISIONS.items() if "No hello" in value or "Do not hello" in value
}
DEFAULT_WS_PATHS = (
    "/ws/join",
    "/ws/agent",
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
    "value",
)
from turn_state_model import TurnState
from v2_contracts import contract_dict
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
GUARDIAN_MARKERS = ("guardian", "monster", "npc", "system")
ROOM_ID_KEYS = ("gameId", "game_id", "roomId", "room_id", "id", "joinIntentId")
ROOM_START_REMAINING_KEYS = (
    "playersNeededToStart",
    "players_needed_to_start",
    "neededToStart",
    "needed_to_start",
    "playersUntilStart",
    "players_until_start",
    "remainingPlayersToStart",
    "remaining_players_to_start",
    "agentsNeededToStart",
    "agents_needed_to_start",
    "agentsUntilStart",
    "agents_until_start",
    "slotsUntilStart",
    "slots_until_start",
)
ROOM_START_THRESHOLD_KEYS = (
    "requiredPlayers",
    "required_players",
    "requiredAgents",
    "required_agents",
    "minPlayers",
    "min_players",
    "minAgents",
    "min_agents",
    "minimumPlayers",
    "minimum_players",
    "minimumAgents",
    "minimum_agents",
    "startPlayerCount",
    "start_player_count",
    "startAgentCount",
    "start_agent_count",
    "startThreshold",
    "start_threshold",
)
ROOM_METADATA_KEYS = (
    "lobby", "config", "settings", "requirements", "match", "start", "metadata", "queue"
)


@dataclass(frozen=True)
class ClawRuntimeConfig:
    api_key: str
    api_base: str = CLAW_API_BASE
    version: str = ""
    mode: str = "offchain"
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


def append_action_audit(entry: dict[str, Any], *, limit: int = 25) -> list[dict[str, Any]]:
    status = read_json(claw_runtime_status_file())
    rows = status.get("action_audit") if isinstance(status.get("action_audit"), list) else []
    cleaned = {
        "ts": int(time.time()),
        "kind": str(entry.get("kind") or "runtime")[:32],
        "action": entry.get("action") if isinstance(entry.get("action"), dict) else {},
        "outcome": entry.get("outcome") if isinstance(entry.get("outcome"), dict) else {},
        "reason": str(entry.get("reason") or "")[:240],
        "state": str(entry.get("state") or status.get("state") or "")[:80],
    }
    rows.append(cleaned)
    rows = rows[-max(1, limit):]
    update_status(action_audit=rows)
    return rows


def runtime_intent(action: dict[str, Any]) -> str:
    action_type = str(action.get("type") or "unknown")
    reason = str(action.get("reason") or "")
    target = action.get("targetId") or action.get("itemId") or action.get("regionId") or ""
    parts = [action_type]
    if target:
        parts.append(str(target)[:48])
    if reason:
        parts.append(reason[:120])
    return " | ".join(parts)


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


def terminal_game_blocked(status: dict[str, Any], game_id: str) -> bool:
    terminal_id = str(status.get("terminal_game_id") or "")
    return bool(terminal_id and game_id and terminal_id == game_id)


def action_envelope(action: dict[str, Any]) -> dict[str, Any]:
    thought = public_action_thought(action)
    data = {key: value for key, value in action.items() if not key.startswith("_")}
    return {"type": "action", "data": data, "thought": thought}


async def coordinate_free_action_send(ws: Any, state: TurnState, action: dict[str, Any]):  # type: ignore[no-untyped-def]
    allowed, request, policy, policy_record = authorize_free_action_execution(state, action)
    if not allowed:
        return None, policy_record
    envelope = action_envelope(action)

    async def send() -> dict[str, Any]:
        await ws.send(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
        return {"code": "sent"}

    return await execute_authorized(request, policy, send), policy_record


def action_signature(action: dict[str, Any], *, turn: int = 0) -> str:
    data = {
        key: value
        for key, value in action.items()
        if not key.startswith("_") and key not in {"reason", "thought"}
    }
    return f"{int(turn)}:{json.dumps(data, sort_keys=True, separators=(',', ':'))}"


def duplicate_action_sent(status: dict[str, Any], action: dict[str, Any], *, turn: int) -> bool:
    if not isinstance(status, dict):
        return False
    return (
        int(status.get("last_action_turn") or -1) == int(turn)
        and str(status.get("last_action_signature") or "") == action_signature(action, turn=turn)
    )


def snapshot_signature(snapshot: dict[str, Any] | None) -> str:
    if not isinstance(snapshot, dict):
        return ""
    try:
        return json.dumps(snapshot, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    except Exception:
        return ""


def free_actions_from_side_effects(action: dict[str, Any], *, limit: int = 2) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for effect in action.get("_side_effects", []):
        if not isinstance(effect, dict) or effect.get("type") != "game_free_action":
            continue
        payload = effect.get("action")
        if isinstance(payload, dict) and payload.get("type"):
            out.append(payload)
        if len(out) >= max(0, limit):
            break
    return out


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
        "rest": shared_public_line("rest", "Hellion pauses only because even nightmares respect cooldowns."),
    }
    if "death-zone" in reason or "death zone" in reason:
        return "Hellion refuses to be seasoned by the arena. Moving."
    if "weapon" in reason or "upgrade" in reason:
        return "Hellion found sharper punctuation."
    if "scout fallback" in reason:
        return "Hellion scouts forward. Standing still is for statues."
    return shared_public_line(
        action_type,
        lines.get(action_type, "Hellion proceeds. The arena may file complaints."),
    )[:THOUGHT_MAX_CHARS]


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
        "alive_count": state.alive_count,
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


def room_id(room: dict[str, Any]) -> str:
    for key in ROOM_ID_KEYS:
        value = str(room.get(key) or "").strip()
        if value:
            return value
    return ""


def stale_paid_room_ids() -> set[str]:
    return {str(item.get("room_id") or "") for item in stale_paid_rooms(limit=100) if item.get("room_id")}


def occupant_looks_guardian(occupant: Any) -> bool:
    if not isinstance(occupant, dict):
        return False
    label = " ".join(
        str(occupant.get(key) or "")
        for key in ("name", "agentName", "type", "kind", "role", "class", "id", "agentId")
    ).lower()
    return any(marker in label for marker in GUARDIAN_MARKERS)


def room_competitor_population(room: dict[str, Any]) -> int | None:
    for key in ROOM_LIST_COUNT_KEYS:
        value = room.get(key)
        if isinstance(value, list):
            return len([item for item in value if not occupant_looks_guardian(item)])
    for key in ("playerCount", "playersCount", "humanCount", "humanPlayers", "competitorCount", "agentCompetitorCount"):
        value = room.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, float):
            return max(0, int(value))
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
    return room_population(room)


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


def _room_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def room_metadata_mappings(room: dict[str, Any]) -> list[dict[str, Any]]:
    mappings = [room]
    mappings.extend(room[key] for key in ROOM_METADATA_KEYS if isinstance(room.get(key), dict))
    return mappings


def room_slots_until_start(room: dict[str, Any]) -> int | None:
    """Return a server-proven start distance; never infer it from room capacity."""
    mappings = room_metadata_mappings(room)
    for mapping in mappings:
        for key in ROOM_START_REMAINING_KEYS:
            remaining = _room_nonnegative_int(mapping.get(key))
            if remaining is not None:
                return remaining

    threshold = next(
        (
            value
            for mapping in mappings
            for key in ROOM_START_THRESHOLD_KEYS
            if (value := _room_nonnegative_int(mapping.get(key))) is not None
        ),
        None,
    )
    competitors = next(
        (
            value
            for mapping in mappings
            if (value := room_competitor_population(mapping)) is not None
        ),
        None,
    )
    if threshold is None or competitors is None:
        return None
    return max(0, threshold - competitors)


def selectable_paid_rooms(welcome: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return unique, addressable paid rooms not already remembered as stale."""
    stale_ids = stale_paid_room_ids()
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for room in room_entries(welcome):
        candidate_id = room_id(room)
        if room_entry_type(room) != "paid" or not candidate_id or candidate_id in stale_ids or candidate_id in seen:
            continue
        seen.add(candidate_id)
        selected.append(room)
    return selected


def room_choice_summary(welcome: dict[str, Any] | None) -> dict[str, Any]:
    paid_populations: list[int] = []
    free_populations: list[int] = []
    paid_competitors: list[int] = []
    free_competitors: list[int] = []
    unknown_rooms = 0
    stale_paid_rooms_seen = 0
    stale_ids = stale_paid_room_ids()
    for room in room_entries(welcome):
        entry_type = room_entry_type(room)
        population = room_population(room)
        competitors = room_competitor_population(room)
        if entry_type == "paid" and room_id(room) in stale_ids:
            stale_paid_rooms_seen += 1
            continue
        if entry_type == "paid" and population is not None:
            paid_populations.append(population)
            paid_competitors.append(competitors if competitors is not None else population)
        elif entry_type == "free" and population is not None:
            free_populations.append(population)
            free_competitors.append(competitors if competitors is not None else population)
        elif entry_type:
            unknown_rooms += 1
    return {
        "paid_rooms": len(paid_populations),
        "paid_occupied": sum(1 for count in paid_competitors if count > 0),
        "paid_total_occupants": sum(paid_populations),
        "paid_competitors": sum(paid_competitors),
        "free_rooms": len(free_populations),
        "free_occupied": sum(1 for count in free_competitors if count > 0),
        "free_total_occupants": sum(free_populations),
        "free_competitors": sum(free_competitors),
        "unknown_rooms": unknown_rooms,
        "stale_paid_rooms": stale_paid_rooms_seen,
    }


def readiness_blocks_free(welcome: dict[str, Any] | None) -> bool:
    readiness = (welcome or {}).get("readiness", {})
    if not isinstance(readiness, dict):
        return False
    free_room = readiness.get("freeRoom") or readiness.get("free_room")
    identity_only_failure = False
    if isinstance(free_room, dict):
        missing = free_room.get("missing") or free_room.get("blockers") or free_room.get("errors")
        blockers = missing if isinstance(missing, list) else []
        non_identity = [item for item in blockers if not _identity_only_blocker(item)]
        identity_only_failure = bool(blockers) and not non_identity
        if free_room.get("ok") is False and (not blockers or non_identity):
            return True
    for key in ("free", "freeReady", "free_ready"):
        if readiness.get(key) is False and not identity_only_failure:
            return True
    errors = readiness.get("errors") or readiness.get("blockers") or readiness.get("missing")
    if isinstance(errors, list):
        return any(not _identity_only_blocker(item) for item in errors)
    return False


def _identity_only_blocker(value: Any) -> bool:
    label = str(value).strip().lower().replace("-", "_")
    return "identity" in label or "erc8004" in label or "no_identity" in label


def free_fallback_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_FREE_FALLBACK_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def avoid_empty_paid_rooms_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_AVOID_EMPTY_PAID_ROOMS", "true").strip().lower() not in {"0", "false", "no", "off"}


def require_competitive_paid_room_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_REQUIRE_COMPETITIVE_PAID_ROOM", "true").strip().lower() not in {"0", "false", "no", "off"}


def paid_last_slot_only_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_PAID_LAST_SLOT_ONLY", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }


def minimum_paid_competitors() -> int:
    try:
        return max(1, int(os.getenv("CLAW_ROYALE_MIN_PAID_COMPETITORS", "1")))
    except ValueError:
        return 1


def paid_room_is_competitive(welcome: dict[str, Any] | None) -> bool:
    if not require_competitive_paid_room_enabled():
        return True
    summary = room_choice_summary(welcome)
    return summary["paid_rooms"] > 0 and summary["paid_competitors"] >= minimum_paid_competitors()


def paid_room_is_last_slot_ready(welcome: dict[str, Any] | None) -> bool:
    """Only approve an unambiguous paid selection that is one player from start."""
    rooms = selectable_paid_rooms(welcome)
    if len(rooms) != 1:
        return False
    room = rooms[0]
    competitors = room_competitor_population(room)
    return (
        competitors is not None
        and competitors >= minimum_paid_competitors()
        and room_slots_until_start(room) == 1
    )


def paid_room_is_joinable(welcome: dict[str, Any] | None) -> bool:
    if not paid_room_is_competitive(welcome):
        return False
    return not paid_last_slot_only_enabled() or paid_room_is_last_slot_ready(welcome)


def should_prefer_free_room(config: ClawRuntimeConfig, welcome: dict[str, Any] | None) -> bool:
    if not free_fallback_enabled() or config.mode == "free" or readiness_blocks_free(welcome):
        return False
    summary = room_choice_summary(welcome)
    readiness = (welcome or {}).get("readiness", {})
    free_ready = isinstance(readiness, dict) and isinstance(readiness.get("freeRoom"), dict) and readiness["freeRoom"].get("ok") is True
    if summary["free_occupied"] <= 0 and not free_ready:
        return False
    if summary["paid_rooms"] == 0 and summary["unknown_rooms"] == 0:
        return True
    return summary["paid_rooms"] > 0 and summary["paid_occupied"] == 0


def should_avoid_paid_room(config: ClawRuntimeConfig, welcome: dict[str, Any] | None) -> bool:
    if config.mode == "free":
        return False
    if paid_last_slot_only_enabled() and not paid_room_is_last_slot_ready(welcome):
        return True
    if not avoid_empty_paid_rooms_enabled():
        return False
    summary = room_choice_summary(welcome)
    if summary["paid_rooms"] == 0:
        return require_competitive_paid_room_enabled()
    if summary["paid_occupied"] == 0:
        return True
    return require_competitive_paid_room_enabled() and summary["paid_competitors"] < minimum_paid_competitors()


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
    if not paid_room_is_joinable(welcome):
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


def account_free_ready(account: dict[str, Any]) -> bool:
    """Identity is optional; only explicit non-identity free blockers stop play."""
    readiness = account.get("readiness") if isinstance(account.get("readiness"), dict) else {}
    free_room = readiness.get("freeRoom") or readiness.get("free_room")
    identity_only_failure = False
    if isinstance(free_room, dict) and free_room.get("ok") is False:
        blockers = free_room.get("missing") or free_room.get("blockers") or free_room.get("errors")
        if not isinstance(blockers, list) or not blockers:
            return False
        identity_only_failure = all(_identity_only_blocker(item) for item in blockers)
        if not identity_only_failure:
            return False
    for key in ("free", "freeReady", "free_ready"):
        if readiness.get(key) is False and not identity_only_failure:
            return False
    return True


def join_blocker_for_account(config: ClawRuntimeConfig, account: dict[str, Any]) -> str:
    if not account.get("ok"):
        return str(account.get("error") or "account status unavailable")
    paid_ready = account_paid_ready(account)
    free_ready = account_free_ready(account)
    if config.mode == "free" and not free_ready:
        return "free join blocked by account readiness"
    if config.mode != "free" and not paid_ready and free_fallback_enabled() and free_ready:
        return ""
    if config.mode != "free" and not paid_ready:
        return "paid join blocked: needs at least 500 sMoltz balance or on-chain paid readiness"
    return ""


def hello_frame(
    config: ClawRuntimeConfig,
    welcome: dict[str, Any] | None = None,
    *,
    paid_account_ready: bool | None = None,
) -> dict[str, Any] | None:
    decision = str((welcome or {}).get("decision") or "").upper()
    if decision in NO_HELLO_DECISIONS:
        return None
    free_ready = not readiness_blocks_free(welcome)
    if decision == "FREE_ONLY":
        return {"type": "hello", "entryType": "free"}
    if decision == "PAID_ONLY":
        if paid_account_ready is False or should_avoid_paid_room(config, welcome):
            return None
        paid_mode = config.mode if config.mode != "free" else _readiness_paid_mode(welcome)
        return {"type": "hello", "entryType": "paid", "mode": paid_mode} if paid_mode else None
    if paid_account_ready is False and config.mode != "free":
        if free_fallback_enabled() and free_ready:
            return {"type": "hello", "entryType": "free"}
        return None
    paid_mode = should_auto_upgrade_to_paid(config, welcome)
    if paid_mode:
        return {"type": "hello", "entryType": "paid", "mode": paid_mode}
    if decision in {"", "ASK_ENTRY_TYPE"} and should_prefer_free_room(config, welcome):
        return {"type": "hello", "entryType": "free"}
    if decision in {"", "ASK_ENTRY_TYPE"} and should_avoid_paid_room(config, welcome):
        if free_ready:
            return {"type": "hello", "entryType": "free"}
        return None
    if config.mode == "free":
        return {"type": "hello", "entryType": "free"} if free_ready else None
    if should_avoid_paid_room(config, welcome):
        return None if not free_ready else {"type": "hello", "entryType": "free"}
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
        if agent_state_terminal(state):
            return False
        if not state.can_take_main_action:
            return False
    if frame_type in {"agent_view", "turn_advanced"}:
        return True
    if snapshot and snapshot.get("canAct") is True:
        return True
    view = snapshot.get("view", {}) if isinstance(snapshot, dict) else {}
    return isinstance(view, dict) and view.get("canAct") is True


def has_free_action_window(state: TurnState | None) -> bool:
    if not isinstance(state, TurnState):
        return False
    try:
        if agent_state_terminal(state):
            return False
        if not state.has_broadcast_channel and not state.visible_items and not state.current_region.items and not state.inventory:
            return False
        return bool(FreeActionCortex().evaluate(state, {}))
    except Exception:
        return False


def agent_state_terminal(state: TurnState | None) -> bool:
    return isinstance(state, TurnState) and (not state.self.is_alive or state.self.hp <= 0)


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
        return active_claw_version()


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


def waiting_games_status(config: ClawRuntimeConfig) -> dict[str, Any]:
    """Read the current waiting-room list without attempting to reserve a game."""
    try:
        payload = ClawRoyaleClient(api_key=config.api_key, base_url=config.base_url).waiting_games(timeout=5.0)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:240]}
    if not isinstance(payload, dict):
        return {"ok": False, "error": "waiting-games response was not an object"}
    return {**payload, "ok": payload.get("ok", True)}


def join_selection_context(
    welcome: dict[str, Any],
    waiting_games: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine welcome gates with a successful, fresh read-only room probe.

    Production paid joins must never fall back to possibly stale room metadata
    embedded in ``welcome``.  If the inspection request fails or has no rooms,
    the paid last-slot gate receives no evidence and therefore fails closed.
    """
    probe_ok = isinstance(waiting_games, dict) and waiting_games.get("ok") is True
    rooms = room_entries(waiting_games) if probe_ok else []
    return {
        "type": welcome.get("type"),
        "decision": welcome.get("decision"),
        "readiness": welcome.get("readiness"),
        "instruction": welcome.get("instruction"),
        "helloDeadlineSec": welcome.get("helloDeadlineSec"),
        "paidRoomProbeVerified": probe_ok,
        "waitingGames": rooms,
    }


def record_stale_paid_waiting_games(account: dict[str, Any]) -> list[dict[str, Any]]:
    games = account.get("currentGames") if isinstance(account.get("currentGames"), list) else []
    recorded: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict):
            continue
        if str(game.get("entryType") or "").lower() != "paid":
            continue
        if str(game.get("gameStatus") or "").lower() not in {"waiting", "queued"}:
            continue
        game_id = str(game.get("gameId") or "")
        if not game_id:
            continue
        recorded = remember_stale_paid_room(game_id, reason="account currentGames paid waiting")
    return recorded


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
    append_action_audit(
        {
            "kind": "action_result",
            "action": last_action,
            "outcome": outcome,
            "reason": str(last_action.get("reason") or ""),
            "state": status.get("state") or "",
        }
    )
    try:
        append_action_postmortem(
            build_action_postmortem(action=last_action, payload=payload, snapshot=snapshot)
        )
    except Exception:
        pass

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
        compiled = compile_lessons(evidence_limit=300, min_count=2)
        if compiled.get("lesson_count"):
            update_status(last_lesson_compile=compiled)
    except Exception:
        return


def server_action_window_open(state: TurnState, server_can_act: bool | None) -> bool:
    """Outcome/event truth may close a stale snapshot's main-action window."""
    return state.can_take_main_action and server_can_act is not False


def accepted_cooldown_action_result(payload: dict[str, Any], status: dict[str, Any]) -> bool:
    """Recognize an accepted cooldown action even when event order is reversed."""
    last_action = status.get("last_action") if isinstance(status.get("last_action"), dict) else {}
    action_type = str(last_action.get("type") or "")
    error_text = str(payload.get("message") or payload.get("error") or "")
    success_raw = payload.get("success", payload.get("ok"))
    return action_type in COOLDOWN_ACTIONS and not error_text and success_raw is not False


def _balance_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def preseason1_auto_claim_enabled() -> bool:
    return os.getenv("CERBERUS_PRESEASON1_AUTO_CLAIM_ENABLED", "true").strip().lower() not in {
        "0", "false", "no", "off"
    }


def preseason1_claim_interval_seconds() -> int:
    try:
        return max(
            0,
            int(
                os.getenv(
                    "CERBERUS_PRESEASON1_CLAIM_INTERVAL_SECONDS",
                    str(DEFAULT_PRESEASON1_CLAIM_INTERVAL_SECONDS),
                )
            ),
        )
    except ValueError:
        return DEFAULT_PRESEASON1_CLAIM_INTERVAL_SECONDS


def run_preseason1_claim_sweep(config: ClawRuntimeConfig, *, force: bool = False) -> dict[str, Any]:
    """Claim reached season tiers without ever blocking matchmaking."""
    if not preseason1_auto_claim_enabled():
        return {"ok": True, "enabled": False, "reason": "CERBERUS_PRESEASON1_AUTO_CLAIM_ENABLED is false"}

    now = int(time.time())
    status = read_json(claw_runtime_status_file())
    last_checked = int(status.get("preseason1_claim_checked_at") or 0)
    interval = preseason1_claim_interval_seconds()
    if not force and last_checked and now - last_checked < interval:
        return {
            "ok": True,
            "enabled": True,
            "skipped": "cooldown",
            "retry_after_seconds": max(0, interval - (now - last_checked)),
        }

    try:
        client = ClawRoyaleClient(api_key=config.api_key, base_url=config.base_url)
        stepped = client.preseason1_quests()
        daily = client.preseason1_daily_quests()
        report = claim_reached_preseason1_points(
            client,
            stepped_payload=stepped,
            daily_payload=daily,
        )
        report.update({"enabled": True, "checked_at": now})
        try:
            report["summary"] = client.preseason1_summary()
        except Exception as exc:
            report["summary_error"] = str(exc)[:240]
    except Exception as exc:
        report = {"ok": False, "enabled": True, "checked_at": now, "error": str(exc)[:500]}
    update_status(preseason1_claim_checked_at=now, preseason1_claims=report)
    return report


def schedule_preseason1_claim_sweep(config: ClawRuntimeConfig, *, force: bool = False) -> bool:
    """Run quest REST work off the matchmaking path and retain the task."""
    try:
        task = asyncio.create_task(asyncio.to_thread(run_preseason1_claim_sweep, config, force=force))
    except RuntimeError:
        return False
    BACKGROUND_TASKS.add(task)
    task.add_done_callback(BACKGROUND_TASKS.discard)
    return True


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
    if stage == "game_ended":
        append_social_event(
            {
                "kind": "match_end",
                "game_id": stored_game_id(),
                "detail": f"game ended with balance delta {round(delta, 6)} and total delta {round(total_delta, 6)}",
            }
        )
        try:
            maintenance = run_postgame_hardening_pass()
        except Exception as exc:
            maintenance = {"ok": False, "error": str(exc)[:500]}
        update_status(
            postgame_maintenance=maintenance,
            postgame_maintenance_pending=False,
        )
    return account


def loadout_optimizer_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_LOADOUT_OPTIMIZER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def loadout_auto_apply_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_LOADOUT_AUTO_APPLY", "true").strip().lower() not in {"0", "false", "no", "off"}


def shop_auto_purchase_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_SHOP_AUTO_PURCHASE", "false").strip().lower() in {"1", "true", "yes", "on"}


def reforge_auto_apply_enabled() -> bool:
    return os.getenv("CLAW_ROYALE_REFORGE_AUTO_APPLY", "false").strip().lower() in {"1", "true", "yes", "on"}


def prejoin_loadout_report(config: ClawRuntimeConfig, account: dict[str, Any] | None = None) -> dict[str, Any]:
    if not loadout_optimizer_enabled():
        return {"ok": True, "enabled": False, "reason": "CLAW_ROYALE_LOADOUT_OPTIMIZER_ENABLED is false"}
    try:
        client = ClawRoyaleClient(api_key=config.api_key, base_url=config.base_url)
        loadout = client.loadout()
        relics = client.inventory_relics(limit=int(os.getenv("CLAW_ROYALE_LOADOUT_RELIC_LIMIT", "15") or 15))
        packs = client.inventory_packs(limit=int(os.getenv("CLAW_ROYALE_LOADOUT_PACK_LIMIT", "5") or 5))
        balance = _balance_float((account or {}).get("balance"))
        plan = build_prejoin_plan(loadout=loadout, relics=relics, packs=packs, balance_smoltz=balance)
        shop_result = execute_shop_recommendations(
            client,
            plan.get("shop", []),
            dry_run=not shop_auto_purchase_enabled(),
        )
        reforge_result = execute_reforge_candidates(
            client,
            plan.get("reforge", []),
            dry_run=not reforge_auto_apply_enabled(),
        )
        if (
            (shop_auto_purchase_enabled() and plan.get("shop"))
            or (reforge_auto_apply_enabled() and plan.get("reforge"))
        ):
            loadout = client.loadout()
            relics = client.inventory_relics(limit=int(os.getenv("CLAW_ROYALE_LOADOUT_RELIC_LIMIT", "15") or 15))
            packs = client.inventory_packs(limit=int(os.getenv("CLAW_ROYALE_LOADOUT_PACK_LIMIT", "5") or 5))
            plan = build_prejoin_plan(loadout=loadout, relics=relics, packs=packs, balance_smoltz=balance)
        apply_result = execute_loadout_operations(
            client,
            plan.get("loadout", {}).get("operations", []),
            dry_run=not loadout_auto_apply_enabled(),
        )
        return {
            "ok": True,
            "enabled": True,
            "auto_apply": loadout_auto_apply_enabled(),
            "shop_auto_purchase": shop_auto_purchase_enabled(),
            "reforge_auto_apply": reforge_auto_apply_enabled(),
            "plan": plan,
            "apply": apply_result,
            "shop": shop_result,
            "reforge": reforge_result,
        }
    except Exception as exc:
        return {"ok": False, "enabled": True, "error": str(exc)[:500]}


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
    status = read_json(claw_runtime_status_file())
    if status.get("postgame_maintenance_pending"):
        try:
            maintenance = run_postgame_hardening_pass()
        except Exception as exc:
            maintenance = {"ok": False, "error": str(exc)[:500]}
        update_status(postgame_maintenance=maintenance, postgame_maintenance_pending=False)
    stale_paid = record_stale_paid_waiting_games(account_status)
    join_blocker = join_blocker_for_account(config, account_status)
    loadout_report = prejoin_loadout_report(config, account_status)
    update_status(
        state="connecting",
        endpoint=url,
        version=config.version,
        mode=config.mode,
        account=account_status,
        preseason1_claims=status.get("preseason1_claims", {}),
        stale_paid_rooms=stale_paid,
        loadout_optimizer=loadout_report,
        last_error="" if loadout_report.get("ok", True) else f"loadout optimizer: {loadout_report.get('error', '')}",
    )
    async with websockets.connect(url, additional_headers=extra_headers, ping_interval=20, ping_timeout=20) as ws:
        gameplay_ready = False
        server_can_act: bool | None = None
        schedule_preseason1_claim_sweep(config)
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
                waiting_games = await asyncio.to_thread(waiting_games_status, config)
                selection_context = join_selection_context(payload, waiting_games)
                frame = None if join_blocker else hello_frame(
                    config,
                    selection_context,
                    paid_account_ready=account_paid_ready(account_status),
                )
                room_summary = room_choice_summary(selection_context)
                paid_last_slot_ready = paid_room_is_last_slot_ready(selection_context)
                if frame and frame.get("entryType") == "paid":
                    room_choice_reason = "paid_last_start_slot_ready"
                elif frame and frame.get("entryType") == "free" and config.mode != "free" and not paid_last_slot_ready:
                    room_choice_reason = "free_fallback_paid_last_slot_unproven"
                elif frame and frame.get("entryType") == "free" and should_prefer_free_room(config, selection_context):
                    room_choice_reason = "occupied_free_room_over_empty_paid_rooms"
                else:
                    room_choice_reason = "server_or_config_default"
                update_status(
                    state="welcomed",
                    last_frame_type=frame_type,
                    join_decision=payload.get("decision", ""),
                    join_readiness=selection_context.get("readiness", {}),
                    join_room_choice=frame.get("entryType", "") if frame else "",
                    join_room_choice_reason=room_choice_reason,
                    join_room_summary=room_summary,
                    join_room_probe={
                        "ok": bool(waiting_games.get("ok")),
                        "room_count": len(room_entries(waiting_games)),
                        "error": str(waiting_games.get("error") or "")[:160],
                    },
                    paid_last_slot_ready=paid_last_slot_ready,
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
                action_status = read_json(claw_runtime_status_file())
                if "not running" in error_text.lower():
                    gameplay_ready = False
                reported_can_act = frame_value(payload, "canAct")
                if frame_type == "can_act_changed" and reported_can_act is not None:
                    server_can_act = bool(reported_can_act)
                elif frame_type == "action_result" and "cooldown" in error_text.lower():
                    server_can_act = False
                if frame_type == "action_result":
                    if accepted_cooldown_action_result(payload, action_status):
                        server_can_act = False
                    record_action_result_learning(payload, status=action_status)
                if is_terminal_game_error(error_text):
                    terminal_id = str(action_status.get("current_game_id") or stored_game_id() or "")
                    clear_game_id()
                    update_status(postgame_maintenance_pending=True)
                    record_account_balance(config, stage="game_ended")
                    schedule_preseason1_claim_sweep(config, force=True)
                    update_status(
                        state="game_ended",
                        last_frame_type=frame_type,
                        current_game_id="",
                        terminal_game_id=terminal_id,
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
            terminal_status = read_json(claw_runtime_status_file())
            if terminal_game_blocked(terminal_status, game_id):
                gameplay_ready = False
                clear_game_id()
                update_status(
                    state="terminal_game_waiting",
                    current_game_id="",
                    gameplay_ready=False,
                    can_act=False,
                    last_error="server-terminal game snapshot suppressed",
                )
                continue
            if game_id and terminal_status.get("terminal_game_id"):
                update_status(terminal_game_id="")
            if game_id:
                remember_game_id(game_id)
            status = game_status(payload, snapshot)
            if is_running_game_status(status):
                gameplay_ready = True
            elif is_non_running_game_status(status):
                gameplay_ready = False
            elif is_terminal_game_error(str(payload.get("message") or payload.get("error") or status)):
                terminal_id = game_id or str(terminal_status.get("current_game_id") or stored_game_id() or "")
                clear_game_id()
                update_status(postgame_maintenance_pending=True)
                record_account_balance(config, stage="game_ended")
                schedule_preseason1_claim_sweep(config, force=True)
                update_status(
                    state="game_ended",
                    last_frame_type=frame_type,
                    current_game_id="",
                    terminal_game_id=terminal_id,
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
                live_map=build_live_map(snapshot),
            )
            state = TurnState.from_snapshot(snapshot) if snapshot else None
            if agent_state_terminal(state):
                terminal_id = game_id or str(terminal_status.get("current_game_id") or stored_game_id() or "")
                clear_game_id()
                update_status(postgame_maintenance_pending=True)
                record_account_balance(config, stage="game_ended")
                schedule_preseason1_claim_sweep(config, force=True)
                update_status(
                    state="game_ended",
                    current_game_id="",
                    terminal_game_id=terminal_id,
                    game_status="ended",
                    gameplay_ready=False,
                    can_act=False,
                    last_error="server snapshot reports terminal agent state",
                )
                await ws.close(code=1000, reason="terminal agent snapshot")
                return
            if state:
                for reconciled in reconcile_reserved_free_actions(state):
                    append_action_audit(
                        {
                            "kind": "execution_reconciled",
                            "action": {"type": reconciled.get("operation"), "target": reconciled.get("target")},
                            "reason": reconciled.get("status"),
                            "state": "playing",
                        }
                    )
            if frame_type == "turn_advanced":
                reported_can_act = frame_value(payload, "canAct")
                server_can_act = bool(reported_can_act) if reported_can_act is not None else None
            snapshot_sig = snapshot_signature(snapshot)
            runtime_status = read_json(claw_runtime_status_file())
            if state and int(runtime_status.get("last_snapshot_turn") or -1) == int(state.turn) and str(runtime_status.get("last_snapshot_signature") or "") == snapshot_sig:
                append_action_audit(
                    {
                        "kind": "snapshot_duplicate_suppressed",
                        "reason": "same turn and same snapshot already processed",
                        "state": "playing",
                    }
                )
                continue
            free_action_window = bool(state and not state.can_take_main_action and has_free_action_window(state))
            main_action_window = bool(state and server_action_window_open(state, server_can_act))
            if snapshot and ((wants_action(payload, snapshot, gameplay_ready=gameplay_ready) and main_action_window) or free_action_window):
                action = cerberus_tick(snapshot)
                turn = int(state.turn if state else 0)
                if duplicate_action_sent(runtime_status, action, turn=turn):
                    append_action_audit(
                        {
                            "kind": "action_duplicate_suppressed",
                            "action": action,
                            "reason": "same turn and same action signature already sent",
                            "state": "playing",
                        }
                    )
                    continue
                envelope = action_envelope(action)
                if state and not state.can_take_main_action and str(action.get("type") or "") not in FREE_ACTIONS:
                    update_status(
                        state="cooldown_waiting",
                        last_error="cooldown active without a free-action opportunity",
                        last_action_candidate=action,
                    )
                    continue
                action_type = str(action.get("type") or "")
                execution_result: dict[str, Any] = {}
                if action_type in ENFORCED_FREE_ACTIONS:
                    coordinated, policy_record = await coordinate_free_action_send(ws, state, action)
                    if coordinated is None or coordinated.status != "accepted":
                        append_action_audit(
                            {
                                "kind": "free_action_execution_suppressed",
                                "action": action,
                                "reason": coordinated.status if coordinated else ",".join(policy_record["policy"].get("reasons") or []),
                                "state": "playing",
                            }
                        )
                        continue
                    execution_result = contract_dict(coordinated)
                else:
                    await ws.send(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
                if str(action.get("type") or "") not in FREE_ACTIONS:
                    server_can_act = False
                append_action_audit({"kind": "action_sent", "action": action, "reason": str(action.get("reason") or ""), "state": "playing", "execution_result": execution_result})
                free_actions = free_actions_from_side_effects(action)
                for free_action in free_actions:
                    if str(free_action.get("type") or "") in ENFORCED_FREE_ACTIONS:
                        execution, policy_record = await coordinate_free_action_send(ws, state, free_action)
                        if execution is None or execution.status != "accepted":
                            append_action_audit(
                                {
                                    "kind": "free_action_execution_suppressed",
                                    "action": free_action,
                                    "reason": execution.status if execution else ",".join(policy_record["policy"].get("reasons") or []),
                                    "state": "playing",
                                }
                            )
                            continue
                        execution_result = contract_dict(execution)
                    else:
                        free_envelope = action_envelope(free_action)
                        await ws.send(json.dumps(free_envelope, ensure_ascii=True, separators=(",", ":")))
                        execution_result = {}
                    append_action_audit(
                        {
                            "kind": "free_action_sent",
                            "action": free_action,
                            "reason": str(free_action.get("reason") or ""),
                            "state": "playing",
                            "execution_result": execution_result,
                        }
                    )
                update_status(
                    last_action=action,
                    last_action_at=int(time.time()),
                    last_action_turn=turn,
                    last_action_signature=action_signature(action, turn=turn),
                    last_snapshot_turn=turn,
                    last_snapshot_signature=snapshot_sig,
                    current_intent=runtime_intent(action),
                    last_public_thought=envelope.get("thought", ""),
                    last_free_actions=free_actions,
                    state="playing",
                )
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
            reconnects = 0
            path_index += 1
            update_status(
                state="reconnecting",
                last_error="websocket closed without exception; rotating endpoint",
                last_failed_path=path,
                next_path=paths[path_index % len(paths)],
                candidate_paths=paths,
            )
            await asyncio.sleep(max(1, config.min_reconnect_seconds))
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
