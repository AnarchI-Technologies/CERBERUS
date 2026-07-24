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
from .legacy_strategy_parity import (
    LEGACY_PROVIDER_ORDER,
    StrategyParityReport,
    build_legacy_strategy_providers,
    compare_legacy_strategy_registry,
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
    "LEGACY_PROVIDER_ORDER",
    "StrategyParityReport",
    "build_legacy_strategy_providers",
    "compare_legacy_strategy_registry",
]
