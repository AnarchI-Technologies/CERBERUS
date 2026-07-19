"""Deterministic Claw loadout, shop, and reforge planning.

This module decides what should happen before a paid join. It is intentionally
separate from the websocket action loop: loadout mutation is a lobby concern,
not a mid-match combat action.
"""

from __future__ import annotations

import os
import hashlib
import json
import uuid
from typing import Any

from claw_contract import AFFIX_POOL, LOADOUT, SHOP_ITEMS


SLOT_NAMES = {0: "red", 1: "green", 2: "blue"}
SLOT_INDEX = {"red": 0, "green": 1, "blue": 2}
PACK_PRIORITY = {
    "moltz": 110,
    "moltz_expert": 110,
    "thorns": 95,
    "scout": 90,
    "item": 84,
    "item_expert": 84,
    "goliath": 70,
}
STAT_WEIGHTS = {
    "ATK": 5.0,
    "ITEM ATK": 4.0,
    "MAX HP": 2.8,
    "MAX EP": 2.6,
    "DEF": 2.0,
    "EXPLORE": 1.4,
}
NEGATIVE_AFFIX_PENALTY = 10.0
EMPTY_AFFIX_PENALTY = 3.0


def _items(payload: Any, *keys: str) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in (*keys, "items", "data", "value", "results", "relics", "packs", "inventory"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _items(value, *keys)
            if nested:
                return nested
    return []


def instance_id(item: dict[str, Any]) -> str:
    for key in ("instanceId", "instance_id", "id", "itemId", "packInstanceId", "relicInstanceId"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def item_equipable(item: dict[str, Any]) -> bool:
    return not bool(
        item.get("isListed")
        or item.get("listed")
        or item.get("marketplaceListingId")
        or item.get("escrowed")
        or item.get("locked")
    )


def _label(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("category", "kind", "type", "typeId", "baseName", "name", "tier")
    ).lower()


def relic_slot(item: dict[str, Any]) -> int | None:
    value = item.get("typeIndex")
    if isinstance(value, int) and value in SLOT_NAMES:
        return value
    if isinstance(value, str) and value.strip().isdigit() and int(value) in SLOT_NAMES:
        return int(value)
    label = _label(item)
    for name, index in SLOT_INDEX.items():
        if name in label or f"relic_{name}" in label:
            return index
    return None


def _affix_stat(affix: dict[str, Any]) -> str:
    raw = str(affix.get("stat") or affix.get("statType") or affix.get("type") or affix.get("name") or "").upper()
    for key, rule in AFFIX_POOL.items():
        if key.upper() in raw:
            return str(rule["stat"]).upper()
    if "ITEM" in raw and "ATK" in raw:
        return "ITEM ATK"
    if "MAX" in raw and "HP" in raw:
        return "MAX HP"
    if "MAX" in raw and "EP" in raw:
        return "MAX EP"
    if "EXPLORE" in raw or "SCOUT" in raw:
        return "EXPLORE"
    if "DEF" in raw:
        return "DEF"
    return "ATK" if "ATK" in raw or "ATTACK" in raw else raw


def _affix_value(affix: dict[str, Any]) -> float:
    for key in ("value", "amount", "delta", "statValue"):
        try:
            return float(affix.get(key))
        except (TypeError, ValueError):
            continue
    direction = str(affix.get("direction") or "").strip()
    for key, rule in AFFIX_POOL.items():
        label = str(affix.get("type") or affix.get("name") or "").lower()
        if key in label:
            average = (float(rule["min"]) + float(rule["max"])) / 2
            return average
    return -1.0 if direction == "-" else 0.0


def relic_score(item: dict[str, Any]) -> float:
    score = 0.0
    affixes = item.get("affixes") if isinstance(item.get("affixes"), list) else []
    if not affixes:
        score -= EMPTY_AFFIX_PENALTY
    for affix in affixes:
        if not isinstance(affix, dict):
            continue
        value = _affix_value(affix)
        stat = _affix_stat(affix)
        weight = STAT_WEIGHTS.get(stat, 1.0)
        score += value * weight
        if value < 0:
            score -= NEGATIVE_AFFIX_PENALTY
    tier = str(item.get("tier") or item.get("rarity") or "").upper()
    if tier == "T1":
        score += 18
    elif tier == "T2":
        score += 10
    elif tier == "T3":
        score += 4
    return round(score, 3)


def pack_score(item: dict[str, Any]) -> float:
    label = _label(item)
    score = 0.0
    for marker, value in PACK_PRIORITY.items():
        if marker in label:
            score = max(score, value)
    tier = str(item.get("tier") or item.get("rarity") or "").upper()
    if tier == "T1":
        score += 18
    elif tier == "T2":
        score += 10
    elif tier == "T3":
        score += 4
    return round(score or 50.0, 3)


def pack_category(item: dict[str, Any]) -> str:
    value = str(item.get("category") or item.get("packCategory") or item.get("type") or "").strip().lower()
    return value.replace(" ", "_").replace("-", "_")


def sub_pack_eligible(item: dict[str, Any], main_pack: dict[str, Any]) -> bool:
    category = pack_category(item)
    main_category = pack_category(main_pack)
    return bool(
        instance_id(item)
        and instance_id(item) != instance_id(main_pack)
        and category not in {"scout", "assassin"}
        and (not category or not main_category or category != main_category)
    )


def _loadout_pack_id(loadout: dict[str, Any], slot: str) -> str:
    if slot == "main":
        keys = ("mainPack", "activeMainPack", "activePack", "pack", "equippedPack")
        nested_keys = ("main", "primary")
    else:
        keys = ("subPack", "activeSubPack", "secondaryPack", "equippedSubPack")
        nested_keys = ("sub", "secondary")
    for key in keys:
        pack = loadout.get(key)
        if pack:
            return instance_id(pack) if isinstance(pack, dict) else str(pack)
    packs = loadout.get("packs") or loadout.get("activePacks") or loadout.get("equippedPacks")
    if isinstance(packs, dict):
        for key in nested_keys:
            pack = packs.get(key)
            if pack:
                return instance_id(pack) if isinstance(pack, dict) else str(pack)
    return ""


def _slot_id(loadout: dict[str, Any], slot: int) -> str:
    slots = loadout.get("slots") or loadout.get("relicSlots") or loadout.get("relics") or {}
    if isinstance(slots, dict):
        value = slots.get(str(slot)) or slots.get(SLOT_NAMES[slot]) or slots.get(slot)
        return instance_id(value) if isinstance(value, dict) else str(value or "")
    if isinstance(slots, list):
        for item in slots:
            if isinstance(item, dict) and relic_slot(item) == slot:
                return instance_id(item)
    return ""


def choose_best_loadout(loadout: dict[str, Any], relics_payload: Any, packs_payload: Any) -> dict[str, Any]:
    relics = [item for item in _items(relics_payload, "relics") if item_equipable(item)]
    packs = [item for item in _items(packs_payload, "packs") if item_equipable(item)]
    sub_pack_id = _loadout_pack_id(loadout, "sub")
    chosen_pack = max((pack for pack in packs if instance_id(pack) != sub_pack_id), key=pack_score, default={})
    current_main_pack_id = _loadout_pack_id(loadout, "main")
    chosen_relics: dict[int, dict[str, Any]] = {}
    for slot in SLOT_NAMES:
        candidates = [item for item in relics if relic_slot(item) == slot]
        chosen_relics[slot] = max(candidates, key=relic_score, default={})
    operations: list[dict[str, Any]] = []
    selected_pack_id = instance_id(chosen_pack)
    pack_id = selected_pack_id or current_main_pack_id
    if selected_pack_id and selected_pack_id != current_main_pack_id:
        operations.append(
            {"type": "set_active_pack", "packInstanceId": selected_pack_id, "score": pack_score(chosen_pack)}
        )
    chosen_sub_pack = {}
    if not sub_pack_id:
        chosen_sub_pack = max(
            (pack for pack in packs if sub_pack_eligible(pack, chosen_pack)),
            key=pack_score,
            default={},
        )
        sub_pack_id = instance_id(chosen_sub_pack)
        if sub_pack_id:
            operations.append(
                {"type": "set_sub_pack", "packInstanceId": sub_pack_id, "score": pack_score(chosen_sub_pack)}
            )
    for slot, relic in chosen_relics.items():
        relic_id = instance_id(relic)
        if relic_id and relic_id != _slot_id(loadout, slot):
            operations.append(
                {
                    "type": "set_relic_slot",
                    "typeIndex": slot,
                    "slot": SLOT_NAMES[slot],
                    "relicInstanceId": relic_id,
                    "score": relic_score(relic),
                }
            )
    return {
        "ok": True,
        "operations": operations,
        "chosen": {
            "pack": {
                "id": pack_id,
                "score": pack_score(chosen_pack) if selected_pack_id else None,
                "source": "inventory_selection" if selected_pack_id else "currently_equipped",
            } if pack_id else {},
            "main_pack": {
                "id": pack_id,
                "score": pack_score(chosen_pack) if selected_pack_id else None,
                "source": "inventory_selection" if selected_pack_id else "currently_equipped",
            } if pack_id else {},
            "sub_pack": {
                "id": sub_pack_id,
                "score": pack_score(chosen_sub_pack) if chosen_sub_pack else None,
                "source": "inventory_selection" if chosen_sub_pack else "currently_equipped",
            } if sub_pack_id else {},
            "relics": {
                SLOT_NAMES[slot]: {
                    "id": instance_id(relic) or _slot_id(loadout, slot),
                    "score": relic_score(relic) if instance_id(relic) else None,
                    "source": "inventory_selection" if instance_id(relic) else "currently_equipped",
                }
                for slot, relic in chosen_relics.items()
                if instance_id(relic) or _slot_id(loadout, slot)
            },
        },
        "complete_full_set": bool(
            pack_id
            and sub_pack_id
            and all(instance_id(chosen_relics[slot]) or _slot_id(loadout, slot) for slot in SLOT_NAMES)
        ),
        "missing_components": [
            component
            for component, ready in (
                ("main_pack", bool(pack_id)),
                ("sub_pack", bool(sub_pack_id)),
                *(
                    (
                        f"{SLOT_NAMES[slot]}_relic",
                        bool(instance_id(chosen_relics[slot]) or _slot_id(loadout, slot)),
                    )
                    for slot in SLOT_NAMES
                ),
            )
            if not ready
        ],
    }


def reforge_candidates(relics_payload: Any, *, max_items: int = 3) -> list[dict[str, Any]]:
    rows = []
    for relic in _items(relics_payload, "relics"):
        if not item_equipable(relic):
            continue
        affixes = relic.get("affixes") if isinstance(relic.get("affixes"), list) else []
        negatives = [affix for affix in affixes if isinstance(affix, dict) and _affix_value(affix) < 0]
        missing = max(0, 3 - len(affixes))
        score = relic_score(relic)
        if negatives or missing or score < 0:
            rows.append(
                {
                    "relicInstanceId": instance_id(relic),
                    "slot": SLOT_NAMES.get(relic_slot(relic) or -1, "unknown"),
                    "score": score,
                    "negative_affixes": len(negatives),
                    "missing_affixes": missing,
                    "recommendedItemKey": "effect_add" if missing else "effect_reroll",
                }
            )
    return sorted(rows, key=lambda item: (item["negative_affixes"], item["missing_affixes"], -item["score"]), reverse=True)[:max_items]


def shop_recommendations(*, balance_smoltz: float, relics_payload: Any, packs_payload: Any) -> list[dict[str, Any]]:
    reserve = float(os.getenv("CERBERUS_LOADOUT_SMOLTZ_RESERVE", "1000") or 1000)
    spendable = max(0.0, balance_smoltz - reserve)
    recommendations: list[dict[str, Any]] = []
    if reforge_candidates(relics_payload) and spendable >= SHOP_ITEMS["reforge_stone_bundle"]["price_smoltz"]:
        recommendations.append(
            {
                "type": "buy_shop_item",
                "item": "reforge_stone_bundle",
                "price_smoltz": SHOP_ITEMS["reforge_stone_bundle"]["price_smoltz"],
                "reason": "owned relics have negative or missing affixes",
            }
        )
    packs = _items(packs_payload, "packs")
    full_pack_pressure = len(packs) < int(LOADOUT["lobby_pack_cap"])
    if full_pack_pressure and spendable >= SHOP_ITEMS["random_pack_ticket"]["price_smoltz"]:
        recommendations.append(
            {
                "type": "buy_shop_item",
                "item": "random_pack_ticket",
                "price_smoltz": SHOP_ITEMS["random_pack_ticket"]["price_smoltz"],
                "reason": "pack inventory has room for stronger paid-game setup",
            }
        )
    return recommendations


def build_prejoin_plan(
    *,
    loadout: dict[str, Any],
    relics: Any,
    packs: Any,
    balance_smoltz: float = 0,
) -> dict[str, Any]:
    loadout_plan = choose_best_loadout(loadout, relics, packs)
    reforge = reforge_candidates(relics)
    shop = shop_recommendations(balance_smoltz=balance_smoltz, relics_payload=relics, packs_payload=packs)
    execution_order = prejoin_execution_order(loadout_plan=loadout_plan, reforge=reforge, shop=shop)
    return {
        "ok": True,
        "loadout": loadout_plan,
        "reforge": reforge,
        "shop": shop,
        "execution_order": execution_order,
        "ready_for_paid": bool(loadout_plan.get("complete_full_set")),
        "needs_inventory_refresh": any(step in execution_order for step in ("shop", "reforge")),
    }


def prejoin_execution_order(
    *,
    loadout_plan: dict[str, Any],
    reforge: list[dict[str, Any]],
    shop: list[dict[str, Any]],
) -> list[str]:
    order: list[str] = []
    if shop:
        order.append("shop")
    if reforge:
        order.append("reforge")
    if loadout_plan.get("operations"):
        order.append("loadout")
    if not order:
        order.append("hold")
    return order


def execute_loadout_operations(client: Any, operations: list[dict[str, Any]], *, dry_run: bool = True) -> dict[str, Any]:
    results = []
    for op in operations:
        op_type = str(op.get("type") or "")
        if dry_run:
            results.append({"ok": True, "dry_run": True, "operation": op})
            continue
        idem = str(op.get("idempotencyKey") or loadout_operation_idempotency_key(op))
        if op_type == "set_active_pack":
            results.append(client.set_active_pack(str(op["packInstanceId"]), idem))
        elif op_type == "set_sub_pack":
            results.append(client.set_sub_pack(str(op["packInstanceId"]), idem))
        elif op_type == "set_relic_slot":
            results.append(client.set_relic_slot(int(op["typeIndex"]), str(op["relicInstanceId"]), idem))
        else:
            results.append({"ok": False, "error": f"unsupported operation: {op_type}", "operation": op})
    return {"ok": all(item.get("ok", True) for item in results if isinstance(item, dict)), "results": results}


def loadout_operation_idempotency_key(operation: dict[str, Any]) -> str:
    canonical = json.dumps(
        {
            key: operation.get(key)
            for key in ("type", "packInstanceId", "typeIndex", "relicInstanceId")
            if operation.get(key) not in (None, "")
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "cerberus-loadout-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def execute_shop_recommendations(client: Any, recommendations: list[dict[str, Any]], *, dry_run: bool = True) -> dict[str, Any]:
    results = []
    for recommendation in recommendations:
        if str(recommendation.get("type") or "") != "buy_shop_item":
            results.append({"ok": False, "error": "unsupported shop recommendation", "recommendation": recommendation})
            continue
        item = str(recommendation.get("item") or "")
        if dry_run:
            results.append({"ok": True, "dry_run": True, "recommendation": recommendation})
            continue
        results.append(client.purchase_shop_listing(item, int(recommendation.get("quantity") or 1), str(uuid.uuid4())))
    return {"ok": all(item.get("ok", True) for item in results if isinstance(item, dict)), "results": results}


def execute_reforge_candidates(client: Any, candidates: list[dict[str, Any]], *, dry_run: bool = True) -> dict[str, Any]:
    results = []
    for candidate in candidates:
        relic_id = str(candidate.get("relicInstanceId") or "")
        item_key = str(candidate.get("recommendedItemKey") or "")
        if not relic_id or not item_key:
            results.append({"ok": False, "error": "missing reforge target", "candidate": candidate})
            continue
        if dry_run:
            results.append({"ok": True, "dry_run": True, "candidate": candidate})
            continue
        results.append(client.reforge_relic(relic_id, item_key, str(uuid.uuid4())))
    return {"ok": all(item.get("ok", True) for item in results if isinstance(item, dict)), "results": results}
