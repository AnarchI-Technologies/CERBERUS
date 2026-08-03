"""Credential-agnostic launcher for the decoupled clawroyale.ai worker."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


TRUE_VALUES = {"1", "true", "yes", "on"}


def enabled() -> bool:
    return os.getenv("CLAWROYALE_PLUGIN_ENABLED", "").strip().lower() in TRUE_VALUES


def wait_until_stopped() -> int:
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        time.sleep(1)
    return 0


def request_status(url: str, authorization: str = "") -> int:
    request = urllib.request.Request(
        url,
        data=json.dumps({"state": {}}).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": authorization} if authorization else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (OSError, urllib.error.URLError):
        return 0


def wait_for_protected_core(tick_url: str) -> None:
    for _attempt in range(30):
        missing = request_status(tick_url)
        wrong = request_status(tick_url, "Bearer intentionally-wrong")
        if missing == 401 and wrong == 401:
            return
        time.sleep(1)
    raise SystemExit("CERBERUS core did not expose a protected tick endpoint")


def main() -> int:
    if not enabled():
        print("clawroyale.ai worker installed; waiting for explicit enablement", flush=True)
        return wait_until_stopped()

    tick_url = os.getenv("CERBERUS_TICK_URL", "").strip()
    if not tick_url.startswith("http://127.0.0.1:") or not tick_url.endswith("/tick"):
        raise SystemExit("CERBERUS_TICK_URL must use a loopback HTTP /tick endpoint")
    if not os.getenv("CERBERUS_HTTP_TOKEN", "").strip():
        raise SystemExit("CERBERUS_HTTP_TOKEN is required when the plugin is enabled")

    wait_for_protected_core(tick_url)

    root = Path(__file__).resolve().parents[1]
    worker = root / "src" / "claw_runtime.py"
    if not worker.is_file():
        raise SystemExit("clawroyale.ai worker entrypoint is missing")

    environment = os.environ.copy()
    environment["CLAW_ROYALE_RUNTIME_ENABLED"] = "true"
    process = subprocess.Popen([sys.executable, str(worker)], env=environment)

    def forward(signum: int, _frame: object) -> None:
        if process.poll() is None:
            process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward)
    signal.signal(signal.SIGINT, forward)
    return int(process.wait())


if __name__ == "__main__":
    raise SystemExit(main())
