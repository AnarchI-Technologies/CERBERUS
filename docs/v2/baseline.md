# CERBERUS v2 Phase 0 baseline

Recorded on 2026-07-18 from `main` on Windows with Python 3.12.

## Reproduction

```text
python -m pip install -r requirements.txt
python -m compileall src data tests main_loop.py quickstart.py sitecustomize.py
python -m unittest discover -s tests -p "test_*.py"
```

Result: 277 tests passed in 68.006 seconds.

Local WSL 2 reproduction on Ubuntu 26.04 LTS with Python 3.14.4: 277 tests
passed in 8.173 seconds with two Windows-only DPAPI tests skipped. GitHub Actions
also passes on `windows-latest` and `ubuntu-latest` after making those DPAPI
expectations platform-aware.

The suite currently writes temporary X authorization artifacts in the working
directory during mocked tests and may rewrite `data/hardened_strategy_rules.json`.
Those side effects are test-harness debt and must not be committed as product
changes.

## Existing pull-request overlap

- PR #1: Claw runtime gap tests.
- PR #2: Runtime memory hardening tests.
- PR #3: Runtime safety test gaps.
- PR #5: Observation-only Claw Royale post-mortems.

PRs #1-#3 overlap the Phase 0 characterization objective. They require a
separate review for unique coverage before closure or consolidation; this branch
does not merge or close them automatically.

## Current release gate

- Windows unit suite: passing.
- Linux unit suite: passing in GitHub Actions and local WSL 2; this is test
  portability evidence, not production-runtime proof.
- Adapter contract tests: absent.
- Replay and resilience suites: incomplete.
- Rollback verification: not yet documented.
