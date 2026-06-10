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

The service uses a 1 GB persistent disk mounted at:

```text
/var/data
```

The default Render memory directory is:

```text
/var/data/.cerberus
```

Suggested Render environment values:

```text
CERBERUS_PIN
CERBERUS_HTTP_TOKEN
CERBERUS_MEMORY_DIR=/var/data/.cerberus
CLAW_ROYALE_LIVE_FEED_URL=https://www.clawroyale.com
CLAW_ROYALE_API_KEY
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
