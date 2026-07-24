# Strategy Isolation

Strategy extraction proceeds one provider family at a time behind the stable
IDs in the ClawRoyale.ai catalog.

The threat family is the first extracted set. Its six strategies are named
callables in `clawroyale_strategies.threat`. `ThreatCortex` is retained as a
compatibility wrapper that evaluates those callables in canonical order.

The compatibility registry detects providers with `evaluate_strategy` and calls
only the requested implementation. Providers not yet extracted continue through
the cached family adapter. This permits gradual migration without changing
arbitration behavior or forcing a physical repository split.

The free-action family is the second extracted set. Weapon equip/pickup and
armor equip/pickup are four separate callables, while `FreeActionCortex`
continues to export the helper functions used by unextracted providers.
