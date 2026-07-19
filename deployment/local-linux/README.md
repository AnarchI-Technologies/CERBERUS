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

