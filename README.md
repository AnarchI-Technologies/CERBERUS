# CERBERUS

## Observation-only Claw Royale post-mortems

At the existing `game_ended` balance checkpoint, CERBERUS now writes a typed
`postmortem` item to the configured long-term memory backend (MongoDB when
enabled, otherwise SQLite). Each record contains the final action expectation,
the observed balance/terminal outcome, a deterministic failure category and
confidence, and a proposed 10-match experiment.
When the terminal frame provides them, the evidence also includes placement,
killer, final HP/EP/alive state, remaining-agent count, and at most five recent
action/outcome summaries. Raw websocket frames are never retained.

This scaffold is intentionally observation-only: post-mortems are not read by
the decision engine, do not alter actions, and are not promoted into hardened
strategy rules. Persistence failures are captured in runtime status and never
interrupt gameplay or postgame maintenance.

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

## Render Launch

The repo includes a Render Blueprint:

```text
render.yaml
```

It defines a small Python web service with:

- `/healthz` for Render health checks
- `/ready` for launch preflight
- `/stats` for dashboard stats
- `/dashboard` for the Hellion dashboard
- `/tick` for guarded JSON turn requests
- an optional always-on Claw Royale runtime worker when `CLAW_ROYALE_RUNTIME_ENABLED=true`

The service uses a 5 GB persistent disk mounted at:

```text
/var/data/.cerberus
```

The default Render memory directory is the disk mount itself:

```text
/var/data/.cerberus
```

That path must match the mounted Render disk. Compact memory, long-term
SQLite memory, current-game state, stream chat, voice-lab state, and runtime
status all resolve through `CERBERUS_MEMORY_DIR`.

Suggested Render environment values:

```text
CERBERUS_PIN
CERBERUS_HTTP_TOKEN
CERBERUS_MEMORY_DIR=/var/data/.cerberus
CLAW_ROYALE_LIVE_FEED_URL=https://www.clawroyale.ai/games
CLAW_ROYALE_SPECTATE_BASE_URL=https://www.clawroyale.ai/games/spect
CLAW_ROYALE_API_KEY
CLAW_ROYALE_RUNTIME_ENABLED=true
CLAW_ROYALE_GAME_MODE=offchain
CLAW_ROYALE_FREE_FALLBACK_ENABLED=true
CLAW_ROYALE_PAID_LAST_SLOT_ONLY=true
CERBERUS_PRESEASON1_AUTO_CLAIM_ENABLED=true
CERBERUS_PRESEASON1_CLAIM_INTERVAL_SECONDS=60
CLAW_ROYALE_WS_PATHS=/ws/agent,/ws/join
AGENTMAIL_API_KEY
AGENTMAIL_INBOX_ID
AGENTMAIL_EMAIL
MOLTBOOK_API_KEY
X_CLIENT_ID
X_CLIENT_SECRET
X_REDIRECT_URI
CERBERUS_AGENT_EOA_PRIVATE_KEY
CERBERUS_OWNER_EOA_PRIVATE_KEY
```

Export values from the local encrypted identity vault:

```powershell
python src\render_env_export.py --format render
```

The local identity vault uses Windows DPAPI, so Render cannot decrypt it directly. Export locally, then paste only the needed values into Render's secret environment settings.

Local service test:

```powershell
$env:PORT = "18080"
$env:CERBERUS_MEMORY_DIR = ".render-test-memory"
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

Then rerun `status` and export Render env values again.

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

Use this before exporting Render environment values so the vault contains the latest Moltbook claim state.

## License

The code is released under the MIT License. See `LICENSE`.

AnarchI is a brand/trademark direction of the project owner. The MIT License grants rights to the software code, not trademark rights in the AnarchI name or branding.
