# CERBERUS v2 execution and audit backlog

This phase moves external effects behind the policy and execution contracts one
low-risk action family at a time. Existing deterministic gameplay behavior is
preserved until each seam has fixture coverage and production evidence.

- [x] Add a timeout-bounded execution coordinator with durable idempotency.
- [x] Route arena broadcasts through policy, coordination, and normalized
  execution results.
- [x] Add a hash-linked append-only audit ledger connecting request, policy,
  execution, and outcome records.
- [x] Route pickup and equip free actions through the same seam.
- [x] Reconcile reserved executions after interruption before allowing retry.
- [x] Add a sanitized operator decision/execution timeline.
- [x] Move one complete Claw Royale workflow behind v2 contracts (pickup/equip/broadcast free-action path).
- [ ] Require sustained production evidence before expanding the seam to
  movement, combat, paid entry, signing, or wallet effects.
  Evidence is now measured by `execution_evidence.py`; expansion remains locked
  pending sufficient production samples and operator review.
