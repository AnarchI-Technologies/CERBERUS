"""Compact live Claw Royale map model for the owner dashboard."""

from __future__ import annotations

import math
import time
from typing import Any

from memory_system import scrub_scalar
from turn_state_model import TurnState


HEX_DX = 92
HEX_DY = 80


def build_live_map(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        return {"ok": False, "reason": "no snapshot", "hexes": [], "updated_at": int(time.time())}
    state = TurnState.from_snapshot(snapshot)
    regions = _region_sources(state)
    hexes = [_hex_for_region(region, index, state) for index, region in enumerate(regions)]
    if not any(item.get("is_current") for item in hexes):
        current = _region_payload(
            {
                "id": state.current_region.id or "current",
                "name": state.current_region.name or "Hellion",
                "terrain": state.current_region.terrain,
                "isDeathZone": state.current_region.is_death_zone,
                "items": state.current_region.items,
                "interactables": state.current_region.interactables,
            },
            state,
        )
        hexes.insert(0, _hex_for_region(current, 0, state))
    return {
        "ok": bool(hexes),
        "game_id": state.game_id,
        "turn": state.turn,
        "focus_agent_id": state.self.id,
        "updated_at": int(time.time()),
        "heartbeat": int(time.time()),
        "hexes": hexes[:80],
        "routes": _route_hints(state),
        "summary": _map_summary(hexes),
        "legend": {
            "H": "Hellion",
            "A": "agent",
            "G": "guardian",
            "!": "monster/threat",
            "W": "weapon",
            "$": "Moltz/sMoltz",
            "R": "relic",
            "P": "pack",
            "+": "heal/medical",
            "B": "broadcast",
            "S": "supply cache",
            "U": "utility",
            "D": "death zone",
            "~": "weather/terrain",
        },
    }


def _map_summary(hexes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "hexes": len(hexes),
        "agents": sum(len([agent for agent in region.get("agents", []) if agent.get("kind") != "self"]) for region in hexes),
        "guardians": sum(len(region.get("monsters", [])) for region in hexes),
        "items": sum(len(region.get("items", [])) for region in hexes),
        "weapons": sum(len([item for item in region.get("items", []) if item.get("kind") == "weapon"]) for region in hexes),
        "currency": sum(len([item for item in region.get("items", []) if item.get("kind") == "currency"]) for region in hexes),
        "relics": sum(len([item for item in region.get("items", []) if item.get("kind") == "relic"]) for region in hexes),
        "packs": sum(len([item for item in region.get("items", []) if item.get("kind") == "pack"]) for region in hexes),
        "medical": sum(len([item for item in region.get("interactables", []) if item.get("kind") == "medical"]) for region in hexes),
        "broadcast": sum(len([item for item in region.get("interactables", []) if item.get("kind") == "broadcast"]) for region in hexes),
        "utilities": sum(len(region.get("interactables", [])) for region in hexes),
        "moltz_targets": sum(len([agent for agent in region.get("agents", []) if agent.get("carries_value")]) for region in hexes),
        "recommended_routes": sum(1 for region in hexes if region.get("recommended")),
        "death_zones": sum(1 for region in hexes if region.get("is_death_zone")),
    }


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("id") or item.get("typeId") or item.get("type") or item.get("name") or len(out))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _dedupe_interactables(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("id") or item.get("type") or item.get("name") or len(out))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _region_sources(state: TurnState) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for region in [
        {
            "id": state.current_region.id,
            "name": state.current_region.name,
            "terrain": state.current_region.terrain,
            "weather": state.current_region.weather,
            "isDeathZone": state.current_region.is_death_zone,
            "items": state.current_region.items,
            "interactables": state.current_region.interactables,
            "connections": state.current_region.connections,
        },
        *[item for item in state.visible_regions if isinstance(item, dict)],
        *[item for item in state.connected_regions if isinstance(item, dict)],
    ]:
        payload = _region_payload(region, state)
        region_id = payload["id"]
        if region_id and region_id not in seen:
            seen.add(region_id)
            out.append(payload)
    return out


def _region_payload(region: dict[str, Any], state: TurnState) -> dict[str, Any]:
    region_id = scrub_scalar(region.get("id") or region.get("regionId") or "", limit=48)
    is_current = bool(region_id and region_id == state.current_region.id) or (
        not region_id and not state.current_region.id and region is not None
    )
    items = [item for item in region.get("items", []) if isinstance(item, dict)] if isinstance(region.get("items"), list) else []
    interactables = (
        [item for item in region.get("interactables", []) if isinstance(item, dict)]
        if isinstance(region.get("interactables"), list)
        else []
    )
    if is_current:
        items = _dedupe_items([*items, *state.visible_items])
        interactables = _dedupe_interactables([*interactables, *state.current_region.interactables])
    visible_agents = [
        agent
        for agent in state.visible_agents
        if (is_current and (not agent.region_id or agent.region_id == region_id)) or (not is_current and agent.region_id == region_id)
    ]
    agents = [_agent_marker(agent) for agent in visible_agents if not _agent_looks_guardian(agent)]
    monsters = [
        _agent_marker(monster)
        for monster in [*state.visible_monsters, *[agent for agent in visible_agents if _agent_looks_guardian(agent)]]
        if (is_current and (not monster.region_id or monster.region_id == region_id)) or (not is_current and monster.region_id == region_id)
    ]
    if is_current and state.self.id:
        agents.insert(
            0,
            {
                "id": state.self.id,
                "name": state.self.name or "Hellion",
                "kind": "self",
                "hp": state.self.hp,
                "ep": state.self.ep,
                "atk": state.self.atk,
                "defense": state.self.defense,
                "symbol": "H",
                "carries_value": False,
            },
        )
    return {
        "id": region_id or "current",
        "name": scrub_scalar(region.get("name") or state.current_region.name or region_id or "Unknown", limit=64),
        "terrain": scrub_scalar(region.get("terrain") or "", limit=48),
        "weather": scrub_scalar(region.get("weather") or "", limit=48),
        "terrain_symbol": _terrain_symbol(str(region.get("terrain") or "")),
        "weather_symbol": _weather_symbol(str(region.get("weather") or "")),
        "is_death_zone": bool(region.get("isDeathZone") or region.get("isDeathzone") or region.get("deathZone")),
        "is_pending_death_zone": region_id in state.pending_deathzone_ids,
        "is_current": is_current,
        "items": [_item_marker(item) for item in items],
        "interactables": [_interactable_marker(item) for item in interactables],
        "agents": agents,
        "monsters": monsters,
        "connections": [scrub_scalar(_connection_id(item), limit=48) for item in region.get("connections", [])]
        if isinstance(region.get("connections"), list)
        else [],
    }


def _hex_for_region(region: dict[str, Any], index: int, state: TurnState) -> dict[str, Any]:
    q, r = _spiral_coord(index)
    x = q * HEX_DX + (r % 2) * (HEX_DX // 2)
    y = r * HEX_DY
    contents = _contents(region)
    score = _loot_score(region)
    return {
        **region,
        "q": q,
        "r": r,
        "x": x,
        "y": y,
        "contents": contents,
        "badges": _badges(region, contents),
        "loot_score": score,
        "recommended": bool(score >= 50 and not region.get("is_death_zone")),
        "danger": _danger_score(region, state),
    }


def _spiral_coord(index: int) -> tuple[int, int]:
    if index <= 0:
        return (0, 0)
    ring = math.ceil((math.sqrt(12 * index + 9) - 3) / 6)
    start = 1 + 3 * ring * (ring - 1)
    offset = index - start
    directions = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]
    q, r = ring, 0
    for dq, dr in directions:
        steps = min(ring, max(0, offset))
        q += dq * steps
        r += dr * steps
        offset -= steps
        if offset <= 0:
            break
    return (q, r)


def _contents(region: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if region.get("is_current"):
        labels.append("H")
    if region.get("is_death_zone"):
        labels.append("D")
    if region.get("monsters"):
        labels.append("G")
    if any(agent.get("kind") != "self" for agent in region.get("agents", [])):
        labels.append("A")
    for item in region.get("items", []):
        kind = item.get("kind")
        if kind == "weapon":
            labels.append("W")
        elif kind == "currency":
            labels.append("$")
        elif kind == "relic":
            labels.append("R")
        elif kind == "pack":
            labels.append("P")
        elif kind == "heal":
            labels.append("+")
        elif kind == "supply":
            labels.append("S")
    if region.get("interactables"):
        for utility in region.get("interactables", []):
            labels.append(str(utility.get("symbol") or "U"))
    if region.get("terrain_symbol") or region.get("weather_symbol"):
        labels.append("~")
    return list(dict.fromkeys(labels))


def _badges(region: dict[str, Any], contents: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for symbol in contents:
        out.append({"symbol": symbol, "kind": "status", "label": symbol, "priority": _symbol_priority(symbol)})
    for item in region.get("items", []):
        out.append(
            {
                "symbol": item.get("symbol") or "?",
                "kind": item.get("kind") or "item",
                "label": item.get("label") or "item",
                "priority": int(item.get("score") or 0),
            }
        )
    for utility in region.get("interactables", []):
        out.append(
            {
                "symbol": utility.get("symbol") or "U",
                "kind": utility.get("kind") or "utility",
                "label": utility.get("label") or "utility",
                "priority": 70,
            }
        )
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for badge in out:
        key = (str(badge.get("symbol") or ""), str(badge.get("label") or ""))
        if key not in unique or int(badge.get("priority") or 0) > int(unique[key].get("priority") or 0):
            unique[key] = badge
    return sorted(unique.values(), key=lambda item: int(item.get("priority") or 0), reverse=True)[:9]


def _symbol_priority(symbol: str) -> int:
    return {"H": 200, "D": 160, "G": 135, "!": 130, "A": 120, "R": 115, "P": 100, "$": 90, "W": 85, "+": 75, "B": 72, "S": 70, "U": 60, "~": 30}.get(symbol, 20)


def _loot_score(region: dict[str, Any]) -> int:
    score = 0
    for item in region.get("items", []):
        score += int(item.get("score") or 0)
    if any("ruin" in str(region.get(key) or "").lower() for key in ("name", "terrain")):
        score += 35
    if region.get("interactables"):
        score += 15
    return score


def _danger_score(region: dict[str, Any], state: TurnState) -> int:
    danger = 0
    if region.get("is_death_zone"):
        danger += 100
    if region.get("is_pending_death_zone"):
        danger += 40
    for agent in region.get("agents", []):
        if agent.get("kind") != "self":
            danger += max(8, int(agent.get("atk") or 0))
    for monster in region.get("monsters", []):
        danger += max(10, int(monster.get("atk") or 0))
    if state.alert_active and region.get("monsters"):
        danger += 25
    return danger


def _route_hints(state: TurnState) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for item in [*state.visible_items, *state.current_region.items]:
        marker = _item_marker(item)
        if marker["score"] >= 50:
            hints.append({"type": "loot", "item_id": marker["id"], "label": marker["label"], "score": marker["score"]})
    for region in state.connected_safe_regions():
        label = f"{region.get('name', '')} {region.get('terrain', '')} {region.get('id', '')}"
        score = 30 if "ruin" in label.lower() else 10
        hints.append({"type": "move", "region_id": region.get("id", ""), "label": scrub_scalar(label, limit=80), "score": score})
    return sorted(hints, key=lambda item: int(item.get("score") or 0), reverse=True)[:10]


def _item_marker(item: dict[str, Any]) -> dict[str, Any]:
    label = scrub_scalar(item.get("typeId") or item.get("type") or item.get("name") or item.get("id") or "item", limit=64)
    lowered = label.lower()
    kind = "item"
    score = 10
    if any(term in lowered for term in ("dagger", "sword", "katana", "bow", "pistol", "sniper", "weapon")):
        kind = "weapon"
        score = 85
        symbol = "W"
    elif "moltz" in lowered or "smoltz" in lowered:
        kind = "currency"
        score = 90
        symbol = "$"
    elif "relic" in lowered:
        kind = "relic"
        score = 115
        symbol = "R"
    elif "pack" in lowered:
        kind = "pack"
        score = 100
        symbol = "P"
    elif "medkit" in lowered or "bandage" in lowered:
        kind = "heal"
        score = 75
        symbol = "+"
    elif any(term in lowered for term in ("supply", "cache", "crate")):
        kind = "supply"
        score = 90
        symbol = "S"
    else:
        symbol = "?"
    return {"id": scrub_scalar(item.get("id") or "", limit=48), "label": label, "kind": kind, "score": score, "symbol": symbol}


def _interactable_marker(item: dict[str, Any]) -> dict[str, Any]:
    label = scrub_scalar(item.get("type") or item.get("name") or item.get("id") or "utility", limit=64)
    lowered = label.lower()
    kind = "utility"
    symbol = "U"
    if any(term in lowered for term in ("medical", "medbay", "hospital", "heal")):
        kind = "medical"
        symbol = "+"
    elif any(term in lowered for term in ("broadcast", "tower", "radio", "signal")):
        kind = "broadcast"
        symbol = "B"
    elif any(term in lowered for term in ("shop", "forge", "reforge")):
        kind = "shop"
        symbol = "U"
    return {
        "id": scrub_scalar(item.get("id") or "", limit=48),
        "label": label,
        "kind": kind,
        "symbol": symbol,
    }


def _agent_marker(agent: Any) -> dict[str, Any]:
    return {
        "id": scrub_scalar(agent.id, limit=48),
        "name": scrub_scalar(agent.name or agent.id[:8], limit=48),
        "kind": "monster" if _agent_looks_guardian(agent) else agent.kind,
        "symbol": _agent_symbol(agent),
        "carries_value": _agent_carries_value(agent),
        "hp": agent.hp,
        "ep": agent.ep,
        "atk": agent.atk,
        "defense": agent.defense,
    }


def _agent_symbol(agent: Any) -> str:
    if getattr(agent, "kind", "") == "self":
        return "H"
    return "G" if _agent_looks_guardian(agent) else "A"


def _agent_carries_value(agent: Any) -> bool:
    raw = getattr(agent, "raw", {})
    label = f"{getattr(agent, 'name', '')} {getattr(agent, 'id', '')}".lower()
    if isinstance(raw, dict):
        label += " " + " ".join(str(raw.get(key) or "") for key in ("moltz", "smoltz", "balance", "loot", "bounty", "carrying"))
    return any(term in label.lower() for term in ("moltz", "smoltz", "bounty", "carrier", "carrying"))


def _terrain_symbol(terrain: str) -> str:
    lowered = terrain.lower()
    if "ruin" in lowered:
        return "R"
    if "water" in lowered or "river" in lowered:
        return "~"
    if "mount" in lowered or "rock" in lowered:
        return "^"
    if "forest" in lowered or "thorn" in lowered:
        return "*"
    return ""


def _weather_symbol(weather: str) -> str:
    lowered = weather.lower()
    if any(term in lowered for term in ("storm", "rain", "fog", "snow", "ash")):
        return "~"
    return ""


def _agent_looks_guardian(agent: Any) -> bool:
    label = f"{getattr(agent, 'name', '')} {getattr(agent, 'id', '')} {getattr(agent, 'kind', '')}".lower()
    raw = getattr(agent, "raw", {})
    if isinstance(raw, dict):
        label += " " + " ".join(str(raw.get(key) or "") for key in ("type", "role", "class", "agentType")).lower()
    return "guardian" in label or "monster" in label or "npc" in label


def _connection_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or item.get("regionId") or "")
    return str(item or "")
