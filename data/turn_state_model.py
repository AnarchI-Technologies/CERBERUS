"""
Normalized Claw Royale turn state.

The parser tolerates legacy RSS snapshots and newer Pre-S1 fields/events. The
raw snapshot is not kept here; only compact, cortex-facing facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _id(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("id") or value.get("_id") or value.get("regionId") or "")
    return str(value or "")


def _as_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off", ""}:
            return False
    return default


@dataclass(slots=True)
class RegionState:
    id: str = ""
    name: str = ""
    terrain: str = ""
    weather: str = ""
    is_death_zone: bool = False
    connections: list[Any] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    interactables: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AgentState:
    id: str = ""
    name: str = ""
    hp: int = 0
    max_hp: int = 100
    ep: int = 0
    max_ep: int = 10
    atk: int = 25
    defense: int = 5
    is_alive: bool = True
    region_id: str = ""
    kind: str = "agent"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuinState:
    id: str = ""
    gauge: int = 0
    max_gauge: int = 3
    occupied_by: str = ""
    is_empty: bool = False
    content_type: str = ""


@dataclass(slots=True)
class TurnState:
    game_id: str = ""
    agent_id: str = ""
    status: str = ""
    turn: int = 0
    self: AgentState = field(default_factory=AgentState)
    current_region: RegionState = field(default_factory=RegionState)
    connected_regions: list[Any] = field(default_factory=list)
    visible_regions: list[dict[str, Any]] = field(default_factory=list)
    visible_agents: list[AgentState] = field(default_factory=list)
    visible_monsters: list[AgentState] = field(default_factory=list)
    visible_items: list[dict[str, Any]] = field(default_factory=list)
    inventory: list[dict[str, Any]] = field(default_factory=list)
    pending_deathzones: list[dict[str, Any]] = field(default_factory=list)
    recent_logs: list[Any] = field(default_factory=list)
    recent_messages: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    ruins: dict[str, RuinState] = field(default_factory=dict)
    alert_gauge: int = 0
    alert_active: bool = False
    can_act: bool = True
    cooldown_remaining_ms: int = 0

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> "TurnState":
        snapshot = _as_dict(snapshot)
        view = _as_dict(snapshot.get("view", snapshot))
        self_raw = _as_dict(view.get("self", {}))
        region_raw = _as_dict(view.get("currentRegion", {}))

        state = cls(
            game_id=str(snapshot.get("gameId") or view.get("gameId") or ""),
            agent_id=str(snapshot.get("agentId") or self_raw.get("id") or ""),
            status=str(snapshot.get("status") or view.get("status") or ""),
            turn=_as_int(snapshot.get("turn") or view.get("turn")),
            self=parse_agent(self_raw),
            current_region=parse_region(region_raw),
            connected_regions=_as_list(
                region_raw.get("connections")
                or region_raw.get("connectedRegions")
                or region_raw.get("neighbors")
                or region_raw.get("adjacentRegions")
                or region_raw.get("exits")
                or view.get("connectedRegions")
                or view.get("neighbors")
                or view.get("adjacentRegions")
                or view.get("exits")
            ),
            visible_regions=[r for r in _as_list(view.get("visibleRegions")) if isinstance(r, dict)],
            visible_agents=[parse_agent(a) for a in _as_list(view.get("visibleAgents")) if isinstance(a, dict)],
            visible_monsters=[parse_agent(m, kind="monster") for m in _as_list(view.get("visibleMonsters")) if isinstance(m, dict)],
            visible_items=[i for i in _as_list(view.get("visibleItems") or region_raw.get("items")) if isinstance(i, dict)],
            inventory=[i for i in _as_list(self_raw.get("inventory")) if isinstance(i, dict)],
            pending_deathzones=[dz for dz in _as_list(view.get("pendingDeathzones")) if isinstance(dz, dict)],
            recent_logs=_as_list(view.get("recentLogs")),
            recent_messages=[m for m in _as_list(view.get("recentMessages")) if isinstance(m, dict)],
            events=[e for e in _as_list(snapshot.get("events") or view.get("events")) if isinstance(e, dict)],
            can_act=_as_bool(snapshot.get("canAct", view.get("canAct", True)), True),
            cooldown_remaining_ms=_as_int(snapshot.get("cooldownRemainingMs") or view.get("cooldownRemainingMs")),
        )
        state.alert_gauge = _as_int(
            view.get("alertGauge")
            or self_raw.get("alertGauge")
            or view.get("alert")
            or self_raw.get("alert")
        )
        alert_active_raw = view.get("alertActive", self_raw.get("alertActive"))
        state.alert_active = _as_bool(alert_active_raw, state.alert_gauge >= 10)
        state._ingest_events()
        return state

    @property
    def pending_deathzone_ids(self) -> set[str]:
        return {_id(zone) for zone in self.pending_deathzones if _id(zone)}

    @property
    def is_in_death_zone(self) -> bool:
        return self.current_region.is_death_zone

    @property
    def is_low_hp(self) -> bool:
        if self.self.hp <= 0:
            return False
        return self.self.hp <= max(35, int(self.self.max_hp * 0.55))

    @property
    def has_broadcast_channel(self) -> bool:
        if any(str(item.get("typeId") or item.get("type") or "").lower() == "megaphone" for item in self.inventory):
            return True
        return any(
            "broadcast" in str(item.get("type") or item.get("name") or "").lower()
            for item in self.current_region.interactables
        )

    @property
    def can_take_main_action(self) -> bool:
        return self.can_act and self.cooldown_remaining_ms <= 0

    def connected_safe_regions(self) -> list[dict[str, Any]]:
        safe = []
        pending = self.pending_deathzone_ids
        visible_by_id = {str(r.get("id")): r for r in self.visible_regions}
        for entry in self.connected_regions:
            region = visible_by_id.get(entry) if isinstance(entry, str) else entry
            region_id = _id(region)
            if not region_id or region_id in pending:
                continue
            if isinstance(region, dict) and region.get("isDeathZone"):
                continue
            safe.append(region if isinstance(region, dict) else {"id": region_id})
        return safe

    def best_heal_item(self) -> dict[str, Any] | None:
        priority = {"medkit": 3, "bandage": 2, "emergency_food": 1, "food": 1}
        best = None
        best_score = 0
        for item in self.inventory:
            item_type = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            score = max((value for key, value in priority.items() if key in item_type), default=0)
            if score > best_score:
                best = item
                best_score = score
        return best

    def _ingest_events(self) -> None:
        for event in self.events:
            event_type = str(event.get("type") or event.get("eventType") or "")
            data = event.get("data") if isinstance(event.get("data"), dict) else event
            if event_type == "alert_gauge_changed" and str(data.get("agentId", self.agent_id)) == self.agent_id:
                self.alert_gauge = _as_int(data.get("alertGauge"), self.alert_gauge)
                self.alert_active = _as_bool(data.get("alertActive"), self.alert_gauge >= 10)
            if event_type == "ruin_state_changed":
                ruin = RuinState(
                    id=str(data.get("ruinId") or data.get("id") or ""),
                    gauge=_as_int(data.get("gauge")),
                    max_gauge=_as_int(data.get("maxGauge"), 3),
                    occupied_by=str(data.get("occupiedBy") or ""),
                    is_empty=_as_bool(data.get("isEmpty")),
                    content_type=str(data.get("contentType") or ""),
                )
                if ruin.id:
                    self.ruins[ruin.id] = ruin


def parse_region(raw: dict[str, Any]) -> RegionState:
    raw = _as_dict(raw)
    return RegionState(
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        terrain=str(raw.get("terrain") or ""),
        weather=str(raw.get("weather") or ""),
        is_death_zone=_as_bool(raw.get("isDeathZone")),
        connections=_as_list(raw.get("connections")),
        items=[i for i in _as_list(raw.get("items")) if isinstance(i, dict)],
        interactables=[i for i in _as_list(raw.get("interactables")) if isinstance(i, dict)],
    )


def parse_agent(raw: dict[str, Any], *, kind: str = "agent") -> AgentState:
    raw = _as_dict(raw)
    return AgentState(
        id=str(raw.get("id") or raw.get("agentId") or ""),
        name=str(raw.get("name") or raw.get("agentName") or ""),
        hp=_as_int(raw.get("hp")),
        max_hp=_as_int(raw.get("maxHp") or raw.get("maxHP") or raw.get("max_hp"), 100),
        ep=_as_int(raw.get("ep")),
        max_ep=_as_int(raw.get("maxEp") or raw.get("maxEP") or raw.get("max_ep"), 10),
        atk=_as_int(raw.get("atk") or raw.get("attack"), 25),
        defense=_as_int(raw.get("def") or raw.get("defense"), 5),
        is_alive=_as_bool(raw.get("isAlive"), True),
        region_id=str(raw.get("regionId") or raw.get("currentRegionId") or ""),
        kind=str(raw.get("kind") or raw.get("type") or kind),
        raw=raw,
    )
