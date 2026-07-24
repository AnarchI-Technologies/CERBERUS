# CERBERUS Runtime Extraction Audit

Status: extraction is not authorized until the seams below pass independently.

## Coupling inventory

`core_loop.py` currently combines provider action contracts, provider state,
strategy selection, memory, social effects, and application orchestration.

`claw_runtime.py` currently combines transport, authentication, provider
protocol handling, execution, post-game processing, and strategy entry.

`execution_coordinator.py` currently combines policy/execution contracts with
CERBERUS persistence, audit storage, and provider state.

The existing `game_adapters` boundary is transitional. Its normalized
observation carries the provider-specific `TurnState` object and therefore
cannot be the portable boundary.

## Safe extraction order

1. Prove the portable interoperability language in isolation.
2. Implement the Claw Royale adapter against only that language.
3. Move Claw Royale strategy registries behind the adapter boundary.
4. Add compatibility bridges from the existing CERBERUS process.
5. Run old and new paths in shadow mode against sanitized replays.
6. Split repositories only after parity, rollback, and operational tests pass.

## Non-negotiable invariants

- The portable kernel imports no CERBERUS or provider module.
- Wire messages remain canonical JSON.
- Capability names and payload schemas belong to adapters.
- The kernel routes declared capabilities but does not interpret them.
- Adapters cannot return results for a different command, capability, or identity.
- Install and publish remain disabled during validation.
