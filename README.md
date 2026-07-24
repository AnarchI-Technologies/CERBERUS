# CERBERUS

CERBERUS is a deterministic agent runtime for game-facing autonomy, identity bootstrapping, secret vaulting, wallet routing, and social/account integrations.

AnarchI is the brand direction behind the system: hardcoding freedom into the systems of tomorrow.

## What It Does

- Runs a deterministic decision loop for game state and action selection.
- Maintains local memory, knowledge, and strategy systems without committing private state.
- Creates and routes purpose-specific EVM wallets.
- Stores generated credentials in an encrypted local vault.
- Bootstraps external identity integrations for Claw Royale, AgentMail, Moltbook, and X OAuth helpers.
- Includes a hardening test suite for behavior, onboarding, wallet routing, OAuth parsing, and failure handling.

## Repository Safety

This repository is configured to keep local secrets and agent memory out of GitHub.

Ignored by default:

- `.env`
- `.venv312/`
- OAuth session files
- generated memory and knowledge artifacts
- `parts.bin/`
- Python caches and test caches

Commit code, tests, docs meant for humans, and configuration examples. Do not commit real vault files, API keys, wallet private keys, inbox credentials, OAuth tokens, or generated memory artifacts.

## Requirements

- Python 3.12
- PowerShell on Windows
- Git

Install Python dependencies from:

```powershell
python -m pip install -r requirements.txt
```

## Local Setup

From the repository root:

```powershell
py -3.12 -m venv .venv312
.\.venv312\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Create a local `.env` from the example:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` with local-only values. The `.env` file is intentionally ignored by Git.

For the current PowerShell session, you can also set values directly:

```powershell
$env:CERBERUS_PIN = "replace-with-your-vault-passphrase"
$env:AGENTMAIL_API_KEY = "replace-with-your-agentmail-key"
```

## Run Tests

```powershell
python -m unittest discover -s tests -v
```

Optional compile check:

```powershell
python -m compileall src data tests main_loop.py quickstart.py sitecustomize.py
```

## Long-Term SQLite Memory

CERBERUS uses the Python standard-library `sqlite3` module for local long-term memory. No extra database package is required.

Initialize or inspect the database:

```powershell
python src\init_memory_db.py
```

By default, the database lives under the Cerberus memory directory:

```text
~/.cerberus/hellion.longterm.sqlite
```

Override it locally or on a host:

```powershell
$env:CERBERUS_MEMORY_DIR = "C:\cerberus-memory"
$env:CERBERUS_LONGTERM_DB = "C:\cerberus-memory\hellion.longterm.sqlite"
```

Long-term memory stores compact facts, lessons, region notes, opponent dossiers, outcome summaries, and error recovery notes. It should not store raw logs, secrets, private keys, OAuth payloads, full emails, or raw game snapshots.

## GitHub Actions

The repository includes a GitHub Actions workflow at:

```text
.github/workflows/tests.yml
```

It runs on pushes, pull requests, and manual dispatch. The workflow installs dependencies, compiles the Python modules, and runs the unit test suite.

## Self-hosted WSL Ubuntu service

The canonical CERBERUS deployment is the local WSL 2 Ubuntu server documented
under `deployment/local-linux`. systemd supervises immutable releases selected
through `/opt/cerberus-current` and `/opt/cerberus-staging-current`.

The service exposes:

- `/healthz` for process health
- `/ready` for launch preflight
- `/stats` for dashboard statistics
- `/dashboard` for the Hellion dashboard
- `/tick` for guarded JSON turn requests

`src/render_app.py` remains the service filename for compatibility; it is not a
Render.com deployment route. Pulse owns the Claw Royale and MoltStation worker
lifecycle inside the systemd-supervised process.

Runtime state resolves through `CERBERUS_MEMORY_DIR`. Production uses the
operator-managed environment profile loaded by `cerberus.service`; staging uses
the isolated `/etc/cerberus/staging.env` profile and separate state.

The local operator commands are:

```bash
deployment/local-linux/cerberusctl status
deployment/local-linux/cerberusctl doctor
deployment/local-linux/cerberusctl history
```

Build, verify, stage, promote, and rollback scripts live in
`deployment/local-linux`. Production promotion requires an immutable release
that passed the complete test suite in staging.

`render.yaml` and `src/render_env_export.py` are retained only as legacy
migration artifacts. Render.com and Railway are not active deployment targets.

Local service test:

```powershell
$env:PORT = "18080"
$env:CERBERUS_MEMORY_DIR = ".local-test-memory"
python src\render_app.py
```

Then open:

```text
http://127.0.0.1:18080/dashboard
```

Run the deterministic profit simulation:

```powershell
python src\profit_simulator.py --games-per-day 61 --target-per-day 1000
```

The simulator uses synthetic, documented assumptions. It does not prove live
earnings, but it gives a repeatable target: if real telemetry averages roughly
20 sMoltz per productive game, Hellion needs about 50 completed games per day;
with the current mixed synthetic scenario set, she needs about 61 games per day
to clear 1000 sMoltz/day.

The dashboard reads `claw_runtime` from `/stats`. If no game is embedded yet,
check the Runtime Blockers panel first; it reports whether the worker is
disabled, reconnecting, blocked on a paid-join signature frame, or waiting for
Claw Royale to send the next active game snapshot.

The owner dashboard also exposes the current intent, recent action audit,
stuck-state doctor, stale paid-room memory, deployment/disk status, and
suggested-edit review controls. Suggested edits are review-only by default:
approve, reject, or archive them from the private dashboard after entering
`CERBERUS_PIN`.

`/stats` also reports `claw_runtime.games_completed`,
`last_balance_delta`, `total_balance_delta`,
`average_balance_delta_per_game`, and `games_needed_for_1000_per_day` after the
runtime observes account balances across completed games.

Pre-join loadout optimizer defaults:

- `CLAW_ROYALE_LOADOUT_OPTIMIZER_ENABLED=true` polls loadout, relic inventory,
  and pack inventory before joining.
- `CLAW_ROYALE_LOADOUT_AUTO_APPLY=true` applies only safe loadout swaps: active
  Main pack and R/G/B relic slots. A loadout is reported as a full set only
  when a Sub pack is also equipped; Main + Sub + all three relics are required
  for any pack or relic effect. CERBERUS does not guess a Sub-pack mutation
  endpoint when the live contract does not expose it.
- `CERBERUS_LOADOUT_SMOLTZ_RESERVE=1000` keeps a minimum sMoltz reserve when
  generating shop recommendations.
- shop purchases and reforge candidates are reported first as recommendations;
  they are not silent spending operations unless `CLAW_ROYALE_SHOP_AUTO_PURCHASE`
  or `CLAW_ROYALE_REFORGE_AUTO_APPLY` is explicitly enabled.

Paid-room safety defaults:

- `CLAW_ROYALE_GAME_MODE=offchain` makes ready paid games the preferred entry
  type while preserving the guarded free fallback below.
- `CLAW_ROYALE_FREE_FALLBACK_ENABLED=true` lets Hellion choose a viable free
  room when paid readiness or room proof is insufficient.
- `CLAW_ROYALE_AVOID_EMPTY_PAID_ROOMS=true` prevents paying into empty paid
  rooms unless explicitly disabled.
- `CLAW_ROYALE_PAID_LAST_SLOT_ONLY=true` requires exactly one non-stale,
  addressable paid room with at least one real competitor and explicit server
  metadata from a successful fresh `GET /games?status=waiting` inspection
  showing exactly one player still needed to start. A failed probe, missing
  metadata, or multiple paid rooms fails closed to free because the hello frame
  cannot target a specific paid room.
- paid waiting games from account status are remembered as stale room IDs and
  shown on the dashboard so Hellion does not keep rejoining the same trap.

PreSeason 1 quest claims default to on. `CERBERUS_PRESEASON1_AUTO_CLAIM_ENABLED`
claims only explicitly reached stepped tiers and completed daily quests after a
match finalizes, then checks again on reconnect. The sweep is idempotent,
rate-limited by `CERBERUS_PRESEASON1_CLAIM_INTERVAL_SECONDS`, and never blocks
matchmaking when the quest API is unavailable.

If Claw publishes a new WebSocket URL, set `CLAW_ROYALE_WS_PATHS` to a
comma-separated list of paths or full `wss://` URLs. The runtime rotates through
the candidates and reports the last failed path plus the next path in `/stats`.

## Identity Bootstrap

After configuring local environment variables and vault access:

```powershell
python src\identity_bootstrap.py
```

Dry-run mode prepares local identity state without external registration. To execute external calls:

```powershell
python src\identity_bootstrap.py --execute
```

Generated wallet secrets and service credentials should live in the encrypted vault, not in Git.

## Claw ERC-8004 Identity Token

ERC-8004 identity is optional as of Claw Royale v1.11.2. A missing token does
not block free rooms. The helper remains available if you want an identity NFT,
but Hellion's free fallback never waits for one.

Check whether Hellion has one attached:

```powershell
$env:CERBERUS_PIN = "your-pin"
python src\claw_identity_token.py status
```

If you mint the token through the Claw portal or an ERC-8004 registry UI, attach the resulting token ID:

```powershell
python src\claw_identity_token.py attach 123456
```

Then rerun `status` and refresh the operator-managed WSL environment profile.

## AgentMail Quickstart

To create or reuse the configured AgentMail inbox:

```powershell
python quickstart.py
```

The inbox ID and email can be stored locally in `.env`.

## X OAuth Helper

The X OAuth helper supports local callback, manual callback exchange, and optional email delivery of authorization URLs.

The helper can read X OAuth values from the current session, `.env`, or persistent Windows User/Machine environment variables. Check without printing secrets:

```powershell
python src\env_doctor.py --x
```

```powershell
python src\x_oauth.py authorize
python src\x_oauth.py authorize --manual-callback
python src\x_oauth.py exchange-url "http://127.0.0.1:8765/x/callback?code=...&state=..."
```

OAuth session files are ignored and should not be committed.

## Moltbook Claim With X OAuth

After X OAuth tokens are stored in the local identity vault, Hellion can post the Moltbook verification text through delegated X access and record the result back into the vault:

```powershell
$env:CERBERUS_PIN = "your-pin"
python src\moltbook_claim_assistant.py claim
```

If the claim code only exists in AgentMail, include inbox lookup:

```powershell
python src\moltbook_claim_assistant.py claim --include-inbox
```

To skip the follow-up Moltbook status check:

```powershell
python src\moltbook_claim_assistant.py claim --no-status
```

Use this before refreshing the WSL environment profile so the vault contains the latest Moltbook claim state.

## License

The code is released under the MIT License. See `LICENSE`.

AnarchI is a brand/trademark direction of the project owner. The MIT License grants rights to the software code, not trademark rights in the AnarchI name or branding.
