# Production proof — 2026-07-18 local operator node

This evidence is sanitized. It contains no game ID, agent ID, wallet address,
credential, signature, or private runtime payload.

## Restart and retry drill

- Preconditions: the server had reported a terminal HP-zero game; CERBERUS had
  persisted the terminal-game quarantine; staging was independently healthy.
- Operation: restart `cerberus.service`, wait for loopback health, then observe
  the connector for 20 seconds while it encountered the same server-side game.
- Production health recovery: **1,160 ms**.
- Staging health during drill: **healthy**.
- Action post-mortems before restart: **30**.
- Action post-mortems after retry window: **30**.
- Terminal-game quarantine after restart: **present**.

Result: no duplicate or stale gameplay action was emitted after restart/retry,
and deterministic service recovery completed well within five minutes.

## Remaining proof

An actual prior-release rollback has not yet been exercised. The documented
rollback procedure remains available, but the backlog item stays open until a
separate release directory or equivalent atomic deployment target permits a
rollback without rewriting the live shared working tree.
