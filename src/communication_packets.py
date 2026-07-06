"""Deterministic packetization for agent-to-agent communication."""

from __future__ import annotations

from typing import Any

from external_wisdom import handoff_packet_policy
from memory_system import scrub_scalar


def evidence_anchor_for_message(text: str) -> str:
    lowered = str(text or "").lower()
    if any(marker in lowered for marker in ("i saw", "just saw", "i watched", "watched", "i killed", "i took")):
        return "seen"
    if any(marker in lowered for marker in ("simulated", "sim", "tested", "repro", "ran ")) and "run" in lowered:
        return "simulated"
    if any(marker in lowered for marker in ("heard", "rumor", "rumour", "someone said", "they said")):
        return "hearsay"
    if any(marker in lowered for marker in ("maybe", "likely", "probably", "i think", "guess", "if ", "watch", "careful")):
        return "inferred"
    return "opponent_claim"


def packet_intent_for_message(text: str, *, helpful: bool = False, alliance_offer: bool = False) -> str:
    lowered = str(text or "").lower()
    if alliance_offer:
        return "alliance_offer"
    if any(marker in lowered for marker in ("betray", "backstab", "cross me", "cross you")):
        return "betrayal_risk"
    if any(marker in lowered for marker in ("watch", "careful", "danger", "incoming", "guardian", "ambush", "exit")):
        return "warning"
    if helpful:
        return "strategy_tip"
    return "comment"


def requested_action_for_message(text: str, *, alliance_offer: bool = False, helpful: bool = False) -> str:
    lowered = str(text or "").lower()
    if alliance_offer:
        return "consider_truce"
    if any(marker in lowered for marker in ("exit", "leave", "run", "escape")):
        return "recheck_exit"
    if any(marker in lowered for marker in ("ruin", "guardian", "alert")):
        return "verify_warning"
    if helpful:
        return "compare_with_board"
    return "log_only"


def claim_from_message(text: str) -> str:
    line = scrub_scalar(text, limit=220)
    if "." in line:
        line = line.split(".", 1)[0].strip()
    if len(line) > 140:
        line = line[:139] + "~"
    return line


def build_handoff_packet(
    text: str,
    *,
    author: str = "",
    helpful: bool = False,
    truthful: bool = False,
    alliance_offer: bool = False,
) -> dict[str, Any]:
    policy = handoff_packet_policy()
    anchor = evidence_anchor_for_message(text)
    packet = {
        "author": scrub_scalar(author, limit=48),
        "intent": packet_intent_for_message(text, helpful=helpful, alliance_offer=alliance_offer),
        "claim": claim_from_message(text),
        "evidence": f"{anchor}:{claim_from_message(text)}",
        "evidence_anchor": anchor,
        "uncertainty": "marked" if truthful else "unmarked",
        "expiry": scrub_scalar(policy.get("default_expiry") or "end_of_match", limit=48),
        "requested_action": requested_action_for_message(text, alliance_offer=alliance_offer, helpful=helpful),
    }
    return packet


def packet_is_complete(packet: dict[str, Any]) -> bool:
    required = handoff_packet_policy().get("required_packet_fields", [])
    if not isinstance(required, list):
        required = []
    return all(scrub_scalar(packet.get(field), limit=120) for field in required)


def packet_summary(packet: dict[str, Any]) -> str:
    return (
        f"packet:{scrub_scalar(packet.get('intent'), limit=24)}:"
        f"{scrub_scalar(packet.get('evidence_anchor'), limit=24)}:"
        f"{scrub_scalar(packet.get('requested_action'), limit=32)}"
    )
