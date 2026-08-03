# Strategy Registry Parity

CERBERUS currently evaluates eleven hardcoded cortex providers in a fixed order.
The compatibility registry exposes their 54 named strategies individually while
caching each provider evaluation once per decision.

Before the core loop can use that registry, `compare_legacy_strategy_registry`
executes two isolated provider sets:

1. the current fixed provider chain;
2. the signal-prefiltered callable registry.

The comparison requires exact equality for candidate order and contents, the
winning candidate, final action, reason, and side effects. Memory and dossier
stores are never shared between the paths.

After parity passed, the core planner was wired to the registry with
`CERBERUS_STRATEGY_EXECUTION=legacy` retained as an immediate fallback. The
registry is the default and reports evaluated-provider, eligible-strategy, and
skipped-strategy metrics. This changes orchestration only; it does not change
install behavior, publication behavior, or strategy implementations.
