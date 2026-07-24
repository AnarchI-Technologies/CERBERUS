# Local rollout and rollback

## Smoke test

Run `bash deployment/local-linux/smoke-test.sh` inside WSL. It verifies that the
production and isolated staging services are active, both loopback health
endpoints answer, and the production-evidence timer is running. It reads no
credentials and performs no game action.

## Rollout

1. Commit a narrow change on an isolated branch.
2. Run the full test suite with production `.env` temporarily unavailable.
3. Confirm source-controlled strategy and knowledge files did not change.
4. Restart staging and run the smoke test.
5. Replay sanitized fixtures and observe shadow-policy results.
6. Restart production only after equivalence checks pass.
7. Record the deployed commit in the operator handoff.

The local operator node can build a secret-free immutable release with
`build-release.sh REPOSITORY GIT_REF`. After the release passes an isolated
loopback smoke test, `activate-release.sh FULL_COMMIT` atomically changes
`/opt/cerberus-current` and restarts production. Releases contain tracked Git
content only; `.env`, runtime memory, wallet identities, and credentials remain
outside the release.

## Rollback

Prefer a new Git revert commit for a published change. On the local operator
node, activate an already-tested prior release commit; do not check out or
rewrite the shared working tree. Verify `/healthz` and confirm runtime status
freshness within five minutes. Do not delete or replace
`/var/data/.cerberus`, `.env`, wallet identities, API keys, or signing material.

If rollback cannot restore health, stop the production service while leaving
staging and the evidence files intact for diagnosis. Financial or identity
operations remain suspended until the operator reviews the incident.
