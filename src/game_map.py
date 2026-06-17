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
            "G": "guardian/monster",
            "W": "weapon",
            "M": "Moltz/sMoltz",
            "R": "ruin/relic",
            "U": "utility",
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
        interactables = [*interactables, *state.current_region.interactables]
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
            },
        )
    return {
        "id": region_id or "current",
        "name": scrub_scalar(region.get("name") or state.current_region.name or region_id or "Unknown", limit=64),
        "terrain": scrub_scalar(region.get("terrain") or "", limit=48),
        "weather": scrub_scalar(region.get("weather") or "", limit=48),
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
    if region.get("monsters"):
        labels.append("G")
    if any(agent.get("kind") != "self" for agent in region.get("agents", [])):
        labels.append("A")
    for item in region.get("items", []):
        kind = item.get("kind")
        if kind == "weapon":
            labels.append("W")
        elif kind == "currency":
            labels.append("M")
        elif kind in {"relic", "pack"}:
            labels.append("R")
    if region.get("interactables"):
        labels.append("U")
    return list(dict.fromkeys(labels))


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
        score = 75
    elif "moltz" in lowered or "smoltz" in lowered:
        kind = "currency"
        score = 90
    elif "relic" in lowered:
        kind = "relic"
        score = 85
    elif "pack" in lowered:
        kind = "pack"
        score = 80
    elif "medkit" in lowered or "bandage" in lowered:
        kind = "heal"
        score = 65
    return {"id": scrub_scalar(item.get("id") or "", limit=48), "label": label, "kind": kind, "score": score}


def _interactable_marker(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": scrub_scalar(item.get("id") or "", limit=48),
        "label": scrub_scalar(item.get("type") or item.get("name") or item.get("id") or "utility", limit=64),
    }


def _agent_marker(agent: Any) -> dict[str, Any]:
    return {
        "id": scrub_scalar(agent.id, limit=48),
        "name": scrub_scalar(agent.name or agent.id[:8], limit=48),
        "kind": "monster" if _agent_looks_guardian(agent) else agent.kind,
        "hp": agent.hp,
        "ep": agent.ep,
        "atk": agent.atk,
        "defense": agent.defense,
    }


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
