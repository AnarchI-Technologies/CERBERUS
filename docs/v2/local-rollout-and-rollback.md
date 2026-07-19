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

## Rollback

Prefer a new Git revert commit for a published change. For an unpublished local
branch, switch to the last recorded known-good commit only after confirming the
working tree is clean. Restart `cerberus.service`, verify `/healthz`, and confirm
runtime status freshness within five minutes. Do not delete or replace
`/var/data/.cerberus`, `.env`, wallet identities, API keys, or signing material.

If rollback cannot restore health, stop the production service while leaving
staging and the evidence files intact for diagnosis. Financial or identity
operations remain suspended until the operator reviews the incident.
