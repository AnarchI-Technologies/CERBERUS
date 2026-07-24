# Canonical self-hosted WSL Ubuntu runtime

CERBERUS runs on the Windows operator node through WSL 2. This is the canonical
self-hosted runtime and remains independent of GitHub's Ubuntu CI runner.

## Measured profile — 2026-07-18

- WSL 2
- Ubuntu 26.04 LTS
- Linux kernel 6.18.33.2-microsoft-standard-WSL2
- Python 3.14.4 in `/opt/cerberus-venv`
- Immutable releases under `/opt/cerberus-releases`
- Production pointer at `/opt/cerberus-current`
- Isolated staging pointer at `/opt/cerberus-staging-current`
- systemd supervision for production, staging, evaluation, and knowledge sync
- Pulse-managed in-process worker lifecycle

## Reproduction

```text
python3 -m venv /opt/cerberus-venv
/opt/cerberus-venv/bin/python -m pip install -r requirements.txt
/opt/cerberus-venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

The cross-platform compact-memory plaintext fallback remains covered. DPAPI
vault behavior is tested on Windows because the implementation intentionally
rejects non-Windows secret-vault use until a vetted Linux crypto backend exists.

WSL is the production application host. Secrets remain outside Git and are
loaded from operator-controlled environment files. Browser-wallet seed phrases
and unrelated personal signing keys must never enter a service profile.

## Release workflow

Use `cerberusctl` to inspect the host and the immutable release scripts to move
a tested commit through build, verification, staging, promotion, and rollback.

```text
deployment/local-linux/cerberusctl status
deployment/local-linux/cerberusctl doctor
deployment/local-linux/build-release.sh <full-commit>
deployment/local-linux/verify-release.sh <full-commit>
deployment/local-linux/activate-staging.sh <full-commit>
deployment/local-linux/promote-production.sh <full-commit>
deployment/local-linux/rollback-production.sh
```

Render.com and Railway are legacy routes and are not part of this workflow.

## Isolated staging service

`cerberus-staging.service` runs a second dashboard on `127.0.0.1:10001` with a
separate memory directory. It runs the release selected atomically by
`activate-staging-release.sh`, independently of production. Its default environment disables every external
runtime, claim, social, loadout, and model effect. It deliberately does not load
the production `.env`, so Hellion's identity and wallet material are absent.

Install the checked-in service and environment template under `/etc`, then
enable it with systemd. A future live A/B agent must receive its own dedicated
environment file and identity; never add those credentials to this template or
reuse Hellion's memory directory.

`cerberus-agent-lab@.service` is the opt-in live A/B template. Each instance
requires a separately protected `/etc/cerberus/agents/<name>.env`, unique port,
Claw account/API key, and wallet identity. The service itself forces a dedicated
`/var/lib/cerberus/agents/<name>` state directory, loopback-only dashboard, local
deterministic model mode, and the current immutable release. Do not place a
browser-extension seed phrase or private key in this file; use only a dedicated
agent credential supported by the official Claw API. No instance is enabled by
default. Start with `CLAW_ROYALE_RUNTIME_ENABLED=false`, verify readiness and
identity separation, then explicitly enable the instance for a bounded test.
Experiment evidence never contains wallet or API material, and candidate support
still requires operator review before production promotion.

Before enabling an experiment instance, run `src/agent_lab_guard.py` against its
protected profile and the production profile. The default pass requires the
runtime to remain disabled. A live pass additionally requires a distinct official
Claw API identity. Signing keys are rejected unless separately and explicitly
allowed; an Edge extension wallet should stay outside the service profile.

