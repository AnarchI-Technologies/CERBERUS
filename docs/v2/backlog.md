# CERBERUS v2 foundation backlog

## Phase 0 — baseline and containment

- [x] Record the Windows baseline test suite.
- [x] Document the current Claw event-to-action path.
- [x] Inventory external effect families and provisional capabilities.
- [x] Add Linux to continuous integration.
- [x] Record the incremental-restructure architecture decision.
- [ ] Review PRs #1-#3 for unique coverage and consolidate or close them.
- [ ] Remove test-suite working-tree side effects.
- [ ] Record deployment smoke-test and rollback procedures.

## Phase 1 — contracts and policy boundary

- [ ] Define versioned Event, Decision, ActionRequest, PolicyDecision,
  ExecutionResult, MemoryRecord, and AuditRecord schemas.
- [ ] Implement `ALLOW`, `DENY`, `REVIEW`, and `DEFER` policy outcomes.
- [ ] Add capability grants and emergency suspension.
- [ ] Route one free Claw Royale action through the policy/execution seam.
- [ ] Add adapter contract and sanitized replay tests.

## Production proof

- [ ] Define the 72-hour evaluation protocol and metrics.
- [ ] Demonstrate no duplicate consequential actions after restart/retry.
- [ ] Demonstrate rollback and recovery within five minutes.
- [ ] Merge behavioral branches only after recorded production evidence.

