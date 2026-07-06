"""Separate topology verifier for live map payloads."""

from __future__ import annotations

from typing import Any

from memory_system import scrub_scalar


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None:
        return []
    return [value]


def _region_id(value: Any) -> str:
    if isinstance(value, dict):
        return scrub_scalar(value.get("id") or value.get("regionId") or "", limit=48)
    return scrub_scalar(value, limit=48)


def verify_live_topology(snapshot: dict[str, Any] | None, map_payload: dict[str, Any] | None) -> dict[str, Any]:
    raw = _as_dict(snapshot)
    view = _as_dict(raw.get("view", raw))
    region = _as_dict(
        view.get("currentRegion")
        or view.get("region")
        or view.get("current_region")
        or view.get("location")
        or raw.get("currentRegion")
        or raw.get("region")
    )
    connected = _as_list(
        region.get("connections")
        or region.get("connectedRegions")
        or region.get("neighbors")
        or region.get("adjacentRegions")
        or region.get("exits")
        or view.get("connectedRegions")
        or view.get("neighbors")
        or view.get("adjacentRegions")
        or view.get("exits")
    )
    pending = [_region_id(item) for item in _as_list(view.get("pendingDeathzones")) if _region_id(item)]
    current_id = _region_id(region.get("id") or region.get("regionId") or "")
    hexes = map_payload.get("hexes", []) if isinstance(map_payload, dict) and isinstance(map_payload.get("hexes"), list) else []
    routes = map_payload.get("routes", []) if isinstance(map_payload, dict) and isinstance(map_payload.get("routes"), list) else []
    hex_ids = {scrub_scalar(item.get("id") or "", limit=48) for item in hexes if isinstance(item, dict) and item.get("id")}
    connected_ids = {_region_id(item) for item in connected if _region_id(item)}
    route_region_ids = {
        scrub_scalar(item.get("region_id") or "", limit=48)
        for item in routes
        if isinstance(item, dict) and item.get("type") == "move" and item.get("region_id")
    }
    missing_connected = sorted(region_id for region_id in connected_ids if region_id not in hex_ids)
    missing_pending = sorted(region_id for region_id in pending if region_id not in hex_ids)
    orphaned_routes = sorted(region_id for region_id in route_region_ids if region_id and region_id not in connected_ids | ({current_id} if current_id else set()))
    pending_marked = sorted(
        scrub_scalar(item.get("id") or "", limit=48)
        for item in hexes
        if isinstance(item, dict) and item.get("is_pending_death_zone") and item.get("id")
    )
    current_present = not current_id or current_id in hex_ids
    return {
        "ok": current_present and not missing_connected and not orphaned_routes,
        "current_region_id": current_id,
        "current_region_present": current_present,
        "connected_regions": sorted(connected_ids),
        "missing_connected_regions": missing_connected,
        "pending_deathzones": pending,
        "pending_marked": pending_marked,
        "missing_pending_deathzones": missing_pending,
        "orphaned_route_targets": orphaned_routes,
        "verifier": "raw_snapshot_topology",
    }
