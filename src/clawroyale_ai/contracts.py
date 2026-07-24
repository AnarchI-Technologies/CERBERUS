"""Claw Royale capability declarations and provider-specific validation."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from interoperability import CapabilitySpec


ADAPTER_ID = "clawroyale.ai"
ADAPTER_VERSION = "1.0.0"
JOIN_CAPABILITY = "clawroyale.session.join"
ACTION_CAPABILITY = "clawroyale.action.submit"
LEAVE_CAPABILITY = "clawroyale.session.leave"

ACTION_TYPES = frozenset(
    {
        "attack",
        "broadcast",
        "equip",
        "explore",
        "interact",
        "move",
        "pickup",
        "rest",
        "talk",
        "use_item",
        "whisper",
    }
)

REQUIRED_ACTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "attack": ("targetId",),
    "broadcast": ("message",),
    "equip": ("itemId",),
    "interact": ("targetId",),
    "move": ("regionId",),
    "pickup": ("itemId",),
    "talk": ("message",),
    "use_item": ("itemId",),
    "whisper": ("targetId", "message"),
}

CAPABILITIES = (
    CapabilitySpec(
        name=JOIN_CAPABILITY,
        version="1.0",
        request_schema={
            "type": "object",
            "required": ["entry_type"],
            "properties": {"entry_type": {"enum": ["free", "paid"]}},
        },
        result_schema={"type": "object"},
    ),
    CapabilitySpec(
        name=ACTION_CAPABILITY,
        version="1.0",
        request_schema={
            "type": "object",
            "required": ["action"],
            "properties": {"action": {"type": "object"}},
        },
        result_schema={"type": "object"},
        observation_schema={"type": "object"},
    ),
    CapabilitySpec(
        name=LEAVE_CAPABILITY,
        version="1.0",
        request_schema={"type": "object"},
        result_schema={"type": "object"},
    ),
)


def validate_action(action: Any, available: frozenset[str] | None = None) -> tuple[str, ...]:
    if not isinstance(action, Mapping):
        return ("action_not_object",)
    action_type = str(action.get("type") or "").strip()
    permitted = available if available is not None else ACTION_TYPES
    if action_type not in permitted:
        return (f"unsupported_action:{action_type or 'missing'}",)
    return tuple(
        f"missing:{field}"
        for field in REQUIRED_ACTION_FIELDS.get(action_type, ())
        if not action.get(field)
    )
