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

## GitHub Actions

The repository includes a GitHub Actions workflow at:

```text
.github/workflows/tests.yml
```

It runs on pushes, pull requests, and manual dispatch. The workflow installs dependencies, compiles the Python modules, and runs the unit test suite.

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

```powershell
python src\x_oauth.py authorize
python src\x_oauth.py authorize --manual-callback
python src\x_oauth.py exchange-url "http://127.0.0.1:8765/x/callback?code=...&state=..."
```

OAuth session files are ignored and should not be committed.

## License

The code is released under the MIT License. See `LICENSE`.

AnarchI is a brand/trademark direction of the project owner. The MIT License grants rights to the software code, not trademark rights in the AnarchI name or branding.
