# ADR 0001: Evolve CERBERUS v2 through incremental boundaries

- Status: Accepted
- Date: 2026-07-18
- Owners: AnarchI Technologies

## Context

CERBERUS already operates deterministic Claw Royale decisions, persistent
memory, identity and wallet services, social integrations, and operator-facing
runtime state. Those responsibilities currently share one application boundary.
A wholesale rewrite would discard characterized safety behavior and make
regression attribution difficult.

## Decision

CERBERUS v2 remains in this repository and evolves incrementally. Claw Royale is
the flagship reference workflow. Existing behavior is first characterized, then
moved behind versioned contracts for events, decisions, action requests, policy
decisions, execution results, memory records, and audit records.

The following constraints apply:

1. Decision production must not gain new external effects during extraction.
2. Consequential effects eventually require an explicit policy decision.
3. Unknown authority, malformed inputs, and stale state fail closed.
4. Model assistance, including local Ollama, may recommend but cannot bypass
   deterministic policy, budgets, capability grants, or execution validation.
5. Experimental architecture remains on isolated branches until production
   evidence supports promotion.
6. The primary deployment is an operator-controlled local node. Ollama is the
   preferred private inference runtime, accessed only through a future central
   gateway. Cloud services are optional sanitized control/observability planes.
7. Ollama unavailability or rejection must leave the deterministic runtime
   operational; models never receive execution authority.

## Consequences

- New integrations are paused during the foundation phase.
- Phase 0 adds evidence and boundaries without relocating working modules.
- Claw Royale adapters will be extracted before less critical integrations.
- Production proof and rollback evidence are required before merging behavioral
  changes to `main`.
