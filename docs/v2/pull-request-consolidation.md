# Early pull-request consolidation

Reviewed through the connected GitHub repository on July 18, 2026.

- **PR #1 — Claw runtime gap tests:** request-ID signing coverage is already
  covered by current signing tests. The unique clean WebSocket-close behavior
  has been consolidated into `tests/test_claw_runtime_gate.py`.
- **PR #2 — runtime memory hardening:** isolated memory routing is already
  superseded by the context-managed runtime override and dedicated staging
  service. Social retry/write-failure behavior is unique but deferred because
  social integration expansion is outside the frozen Claw flagship scope.
- **PR #3 — runtime safety gaps:** the useful render, social exception, and
  isolated-runtime tests are already on `main`. The remaining diff duplicates
  `private_key` and `wallet_address` constructor keywords and must not merge.

These PRs should be closed after the consolidated branch is published. They do
not contain production proof and should not be merged independently.
