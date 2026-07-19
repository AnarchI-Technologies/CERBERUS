# CERBERUS v2 foundation backlog

## Phase 0 — baseline and containment

- [x] Record the Windows baseline test suite.
- [x] Document the current Claw event-to-action path.
- [x] Inventory external effect families and provisional capabilities.
- [x] Add Linux to continuous integration and reproduce it locally in WSL 2.
- [x] Record the incremental-restructure architecture decision.
- [x] Review PRs #1-#3 and consolidate unique in-scope coverage; close them after publication.
- [ ] Remove test-suite working-tree side effects.
- [x] Record deployment smoke-test and rollback procedures.
- [x] Record the initial Windows operator-node hardware profile.
- [x] Record deterministic action post-mortems with bounded, observation-only experiments.
- [x] Install and benchmark evaluation-only Ollama candidates.
- [x] Build a repeatable Ollama health/readiness check with no-model fallback.

## Phase 1 — contracts and policy boundary

- [x] Define versioned Event, Decision, ActionRequest, PolicyDecision,
  ExecutionResult, MemoryRecord, and AuditRecord schemas.
- [x] Implement `ALLOW`, `DENY`, `REVIEW`, and `DEFER` policy outcomes.
- [x] Add capability grants and emergency suspension to the isolated policy engine.
- [ ] Route one free Claw Royale action through the policy/execution seam.
- [x] Add the provider-neutral game adapter contract and sanitized Claw replay tests.
- [x] Add a single model gateway; prohibit direct Ollama calls elsewhere.
- [x] Add pinned model aliases, deadlines, schema validation, and an inference
  kill switch.
- [ ] Require semantic evaluation, not merely schema validity, before model
  promotion.

## Phase 2 — memory and retrieval boundary

- [x] Add classified memory admission with provenance, retention, secret, and
  prompt-injection checks.
- [ ] Route one existing knowledge write through admission in shadow mode.
- [ ] Add local embedding retrieval with source IDs and freshness.
- [ ] Define retention compaction and deletion tests per memory class.

## Production proof

- [x] Define the 72-hour evaluation protocol and metrics.
- [ ] Demonstrate no duplicate consequential actions after restart/retry.
- [ ] Demonstrate rollback and recovery within five minutes.
- [ ] Merge behavioral branches only after recorded production evidence.
