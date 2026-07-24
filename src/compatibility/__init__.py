"""Temporary compatibility seams used while boundaries are proven."""

from .clawroyale_shadow import (
    ClawRoyaleShadowBridge,
    ShadowCheck,
    ShadowReport,
)
from .legacy_strategy_registry import (
    LegacyStrategyRegistry,
    RegistryEvaluation,
    StrategyCallCache,
    StrategyRegistryError,
    state_signals,
)

__all__ = [
    "ClawRoyaleShadowBridge",
    "LegacyStrategyRegistry",
    "RegistryEvaluation",
    "ShadowCheck",
    "ShadowReport",
    "StrategyCallCache",
    "StrategyRegistryError",
    "state_signals",
]
