"""Deterministic runtime evidence and suggested-edit generation.

Hellion is allowed to notice patterns and ask for repairs. She is not allowed
to patch her own code at runtime.
"""

from __future__ import annotations

from typing import Any

from claw_contract import action_cost
from claw_contract import ERROR_CODES
from runtime_state import append_match_evidence, append_suggested_edit
from turn_state_model import TurnState


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _action_type(action: dict[str, Any]) -> str:
    return _text(action.get("type") or "unknown", 40)


def compact_state_summary(state: TurnState | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(state, TurnState):
        turn_state = state
    elif isinstance(state, dict):
        turn_state = TurnState.from_snapshot(state)
    else:
        return {}
    return {
        "game_id": turn_state.game_id,
        "turn": turn_state.turn,
        "agent_id": turn_state.self.id,
        "hp": turn_state.self.hp,
        "max_hp": turn_state.self.max_hp,
        "ep": turn_state.self.ep,
        "max_ep": turn_state.self.max_ep,
        "atk": turn_state.self.atk,
        "region_id": turn_state.current_region.id,
        "region_name": turn_state.current_region.name,
        "terrain": turn_state.current_region.terrain,
        "death_zone": turn_state.is_in_death_zone,
        "visible_agents": len(turn_state.visible_agents),
        "visible_monsters": len(turn_state.visible_monsters),
        "visible_items": len(turn_state.visible_items) + len(turn_state.current_region.items),
        "inventory": len(turn_state.inventory),
        "can_act": turn_state.can_take_main_action,
    }


def match_evidence_entry(
    state: TurnState | dict[str, Any] | None,
    action: dict[str, Any],
    *,
    outcome: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> dict[str, Any]:
    runtime = runtime if isinstance(runtime, dict) else {}
    outcome = outcome if isinstance(outcome, dict) else {}
    return {
        "kind": "turn_observation",
        "state": compact_state_summary(state),
        "action": {
            "type": _action_type(action),
            "targetId": _text(action.get("targetId"), 80),
            "targetType": _text(action.get("targetType"), 40),
            "regionId": _text(action.get("regionId"), 80),
            "itemId": _text(action.get("itemId"), 80),
            "reason": _text(action.get("reason") or action.get("thought"), 220),
            "rejected": action.get("_rejected_action") if isinstance(action.get("_rejected_action"), dict) else {},
            "warnings": action.get("_warnings") if isinstance(action.get("_warnings"), list) else [],
        },
        "outcome": {
            "ok": outcome.get("ok"),
            "code": _text(outcome.get("code") or outcome.get("errorCode") or outcome.get("status"), 80),
            "message": _text(outcome.get("message") or outcome.get("error"), 220),
        },
        "runtime": {
            "state": _text(runtime.get("state"), 80),
            "last_error": _text(runtime.get("last_error"), 220),
            "frame": _text(runtime.get("last_frame_type"), 80),
        },
    }


def suggested_edits_from_observation(
    state: TurnState | dict[str, Any] | None,
    action: dict[str, Any],
    *,
    outcome: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    evidence = match_evidence_entry(state, action, outcome=outcome, runtime=runtime)
    action_type = _action_type(action)
    reason = _text(action.get("reason") or "", 300).lower()
    rejected = action.get("_rejected_action") if isinstance(action.get("_rejected_action"), dict) else {}
    warnings = action.get("_warnings") if isinstance(action.get("_warnings"), list) else []
    outcome = outcome if isinstance(outcome, dict) else {}
    runtime = runtime if isinstance(runtime, dict) else {}
    outcome_code = _text(outcome.get("code") or outcome.get("errorCode") or outcome.get("status"), 80).upper()
    outcome_text = _text(outcome.get("message") or outcome.get("error") or outcome.get("code"), 320).lower()
    runtime_error = _text(runtime.get("last_error"), 320).lower()
    suggestions: list[dict[str, Any]] = []

    if "out-of-range attack" in reason or rejected.get("type") == "attack" and "range" in reason:
        suggestions.append(
            {
                "detector": "legalizer.out_of_range_attack",
                "title": "Tighten attack target scoring before action selection",
                "file": "src/combat_decider.py",
                "symptom": "A planned attack was rejected because the target was out of range.",
                "suggested_change": "Prefer only targets confirmed by target_in_attack_range, then fall back to weapon pickup, movement, or rest.",
                "priority": "high",
                "evidence": evidence,
            }
        )

    if action_type == "rest" and "waiting for usable live turn facts" in reason:
        suggestions.append(
            {
                "detector": "parser.usable_turn_facts",
                "title": "Extend snapshot parsing aliases for live Claw frames",
                "file": "data/turn_state_model.py",
                "symptom": "A live tick had too little normalized state, so Hellion rested instead of acting.",
                "suggested_change": "Compare the raw frame keys against TurnState aliases and add missing self, region, inventory, or canAct mappings.",
                "priority": "critical",
                "evidence": evidence,
            }
        )

    if action_type == "rest" and ("cooldown" in outcome_text or "cooldown" in runtime_error):
        suggestions.append(
            {
                "detector": "runtime.cooldown_waste",
                "title": "Avoid main-action sends while Claw cooldown is active",
                "file": "src/claw_runtime.py",
                "symptom": "The runtime observed cooldown rejection or cooldown wait state.",
                "suggested_change": "Gate sends on canAct/cooldownRemainingMs and reserve free-action thoughts for cooldown windows.",
                "priority": "medium",
                "evidence": evidence,
            }
        )

    if "target_blocked" in outcome_text or "target blocked" in outcome_text:
        suggestions.append(
            {
                "detector": "runtime.target_blocked",
                "title": "Add blocked-target memory penalty",
                "file": "src/decision_engine.py",
                "symptom": "Claw rejected an action because the selected target or route was blocked.",
                "suggested_change": "Record the blocked target or region for the current game and reduce its score until the board changes.",
                "priority": "high",
                "evidence": evidence,
            }
        )

    if any(isinstance(item, dict) and item.get("type") == "save_error" for item in warnings):
        suggestions.append(
            {
                "detector": "storage.save_error",
                "title": "Harden runtime disk write failure path",
                "file": "src/runtime_state.py",
                "symptom": "A memory or dashboard write failed during tick processing.",
                "suggested_change": "Keep the action live, report storage health on the owner dashboard, and retry only compact writes.",
                "priority": "medium",
                "evidence": evidence,
            }
        )

    if "join onchain failed" in runtime_error or "insufficient_balance" in runtime_error:
        suggestions.append(
            {
                "detector": "runtime.paid_join_blocked",
                "title": "Improve paid-game readiness fallback",
                "file": "src/claw_runtime.py",
                "symptom": "Paid join failed before gameplay started.",
                "suggested_change": "Recheck account balance and game mode before retry; fall back to free rooms when paid readiness is false.",
                "priority": "critical",
                "evidence": evidence,
            }
        )

    # Mapping specific ERROR_CODES to suggested architectural repairs
    if outcome_code == "INSUFFICIENT_EP" or "insufficient ep" in outcome_text:
        cost = action_cost(action_type, terrain=_text(evidence.get("state", {}).get("terrain")))
        suggestions.append(
            {
                "detector": "contract.insufficient_ep",
                "title": f"Predictive EP cost validation (Action Cost: {cost})",
                "file": "src/ep_economy_engine.py",
                "symptom": f"Server rejected action: {ERROR_CODES.get('INSUFFICIENT_EP')}",
                "suggested_change": "Incorporate action_cost() check into cortex evaluation to avoid sending actions that exceed current EP.",
                "priority": "high",
                "evidence": evidence,
            }
        )

    if outcome_code == "NO_IDENTITY":
        suggestions.append(
            {
                "detector": "contract.no_identity",
                "title": "Refresh stale Claw identity gating",
                "file": "src/claw_runtime.py",
                "symptom": f"Server rejected join: {ERROR_CODES.get('NO_IDENTITY')}",
                "suggested_change": "Refresh the live Claw version and contract; ERC-8004 is optional and must not block free fallback.",
                "priority": "critical",
                "evidence": evidence,
            }
        )

    if outcome_code == "VERSION_MISMATCH":
        suggestions.append(
            {
                "detector": "contract.version_mismatch",
                "title": "Force version reconciliation on socket close",
                "file": "src/claw_config.py",
                "symptom": f"Server rejected handshake: {ERROR_CODES.get('VERSION_MISMATCH')}",
                "suggested_change": "Call reconcile_claw_version() immediately when a 426 or VERSION_MISMATCH code is observed.",
                "priority": "high",
                "evidence": evidence,
            }
        )

    if outcome_code == "RATE_LIMITED":
        suggestions.append(
            {
                "detector": "contract.rate_limited",
                "title": "Throttle non-essential social messages",
                "file": "src/social_cortex.py",
                "symptom": f"Server connection throttled: {ERROR_CODES.get('RATE_LIMITED')}",
                "suggested_change": "Introduce a local cooldown for talk/whisper actions that shared the 120 msg/min budget with cooldown actions.",
                "priority": "medium",
                "evidence": evidence,
            }
        )

    return suggestions


def record_autonomy_observation(
    state: TurnState | dict[str, Any] | None,
    action: dict[str, Any],
    *,
    outcome: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
) -> None:
    if not isinstance(action, dict):
        return
    evidence = match_evidence_entry(state, action, outcome=outcome, runtime=runtime)
    append_match_evidence(evidence)
    for suggestion in suggested_edits_from_observation(state, action, outcome=outcome, runtime=runtime):
        append_suggested_edit(suggestion)
