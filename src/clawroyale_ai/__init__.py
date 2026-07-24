"""Independent ClawRoyale.ai adapter and strategy package."""

from .adapter import ClawRoyaleAdapter, Transport
from .contracts import (
    ACTION_CAPABILITY,
    ACTION_TYPES,
    ADAPTER_ID,
    ADAPTER_VERSION,
    CAPABILITIES,
    JOIN_CAPABILITY,
    LEAVE_CAPABILITY,
    REQUIRED_ACTION_FIELDS,
    validate_action,
)
from .strategies import Strategy, StrategyError, StrategyRegistry, StrategySelection

__all__ = [
    "ACTION_CAPABILITY",
    "ACTION_TYPES",
    "ADAPTER_ID",
    "ADAPTER_VERSION",
    "CAPABILITIES",
    "JOIN_CAPABILITY",
    "LEAVE_CAPABILITY",
    "REQUIRED_ACTION_FIELDS",
    "ClawRoyaleAdapter",
    "Strategy",
    "StrategyError",
    "StrategyRegistry",
    "StrategySelection",
    "Transport",
    "validate_action",
]
