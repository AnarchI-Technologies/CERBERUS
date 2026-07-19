# Local Linux development profile

CERBERUS Linux compatibility can be reproduced on the Windows operator node
through WSL 2 without replacing GitHub's independent Ubuntu runner.

## Measured profile — 2026-07-18

- WSL 2
- Ubuntu 26.04 LTS
- Linux kernel 6.18.33.2-microsoft-standard-WSL2
- Python 3.14.4 in `/opt/cerberus-venv`
- 277 tests passed in 8.173 seconds
- Two DPAPI-specific tests skipped by explicit Windows platform guards

## Reproduction

```text
python3 -m venv /opt/cerberus-venv
/opt/cerberus-venv/bin/python -m pip install -r requirements.txt
/opt/cerberus-venv/bin/python -m unittest discover -s tests -p "test_*.py"
```

The cross-platform compact-memory plaintext fallback remains covered. DPAPI
vault behavior is tested on Windows because the implementation intentionally
rejects non-Windows secret-vault use until a vetted Linux crypto backend exists.

WSL is a development and compatibility environment. It is not yet an approved
production secret or signing host.

## Isolated staging service

`cerberus-staging.service` runs a second dashboard on `127.0.0.1:10001` with a
separate memory directory. Its default environment disables every external
runtime, claim, social, loadout, and model effect. It deliberately does not load
the production `.env`, so Hellion's identity and wallet material are absent.

Install the checked-in service and environment template under `/etc`, then
enable it with systemd. A future live A/B agent must receive its own dedicated
environment file and identity; never add those credentials to this template or
reuse Hellion's memory directory.

`cerberus-agent-lab@.service` is the opt-in live A/B template. Each instance
requires a separately protected `/etc/cerberus/agents/<name>.env`, unique port,
memory directory, Claw account/API key, and wallet identity. No instance is
enabled by default. Experiment evidence never contains wallet or API material,
and candidate support still requires operator review before production promotion.

