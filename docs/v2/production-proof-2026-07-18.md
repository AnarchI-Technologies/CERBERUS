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

## Prior-release rollback drill

- Built credential-free immutable releases for the current commit and prior
  known-good commit `c9de016…` under `/opt/cerberus-releases`.
- Smoke-tested the current release on isolated loopback port 10002 with all
  external workers disabled and no production environment file.
- Activated the prior release atomically without changing the Git working tree.
- Prior-release rollback health recovery: **443 ms**.
- Observation window after rollback: **15 seconds**.
- Action post-mortems before and after rollback: **30 → 30**.
- Production and staging during rollback: **healthy**.
- Roll-forward health recovery: **338 ms**.
- Production, staging, evaluation timer, and official-knowledge timer after
  roll-forward: **healthy**.

Result: rollback and recovery completed within five minutes without copying or
changing `.env`, runtime memory, wallet identity, API keys, or signing data.
