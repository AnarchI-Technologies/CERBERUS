"""Complete v2 contract construction for low-risk Claw free actions."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from turn_state_model import TurnState
from v2_contracts import ActionRequest, Decision, Event


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_free_action_contracts(
    state: TurnState, action: dict
) -> tuple[Event, Decision, ActionRequest]:
    action_type = str(action.get("type") or "")
    target = str(action.get("targetId") or action.get("regionId") or action.get("itemId") or "")
    correlation_id = str(state.game_id or f"turn-{state.turn}")
    canonical = json.dumps(
        {
            "game": correlation_id,
            "turn": state.turn,
            "actor": state.self.id or state.agent_id,
            "action": {key: value for key, value in action.items() if not str(key).startswith("_") and key != "reason"},
        },
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    event = Event(
        event_id=f"claw-event-{digest[:24]}",
        source="claw_royale.snapshot",
        observed_at=_now(),
        correlation_id=correlation_id,
        payload={"turn": state.turn, "action_type": action_type},
        trust="official_server",
        freshness_seconds=0.0,
    )
    decision = Decision(
        decision_id=f"claw-decision-{digest[:24]}",
        event_id=event.event_id,
        intent=f"execute_free_{action_type}",
        selected_route=action_type,
        confidence=1.0,
        evidence_refs=(event.event_id,),
        assisted_by_model=False,
    )
    request = ActionRequest(
        request_id=f"claw-request-{digest[:24]}",
        decision_id=decision.decision_id,
        correlation_id=correlation_id,
        actor_id=str(state.self.id or state.agent_id or "unknown"),
        capability="game.action.execute",
        provider="claw_royale",
        operation=action_type,
        target=target,
        consequential=False,
        origin="deterministic",
        idempotency_key=digest,
    )
    return event, decision, request
