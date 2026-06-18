"""Independent deterministic Moltbook social queue worker."""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from social_runtime import drain_social_queue_once, social_queue


def worker_enabled() -> bool:
    return os.getenv("CERBERUS_SOCIAL_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}


def run_once(*, max_items: int = 5, client: Any = None) -> dict[str, Any]:
    result = drain_social_queue_once(client=client, max_items=max_items)
    result["queued"] = len([item for item in social_queue() if str(item.get("status") or "queued") == "queued"])
    return result


def run_loop(
    *,
    interval_seconds: int = 60,
    max_items: int = 5,
    stop_after: int = 0,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if not worker_enabled():
        return {"ok": True, "enabled": False, "processed": 0, "reason": "CERBERUS_SOCIAL_WORKER_ENABLED is false"}
    iterations = 0
    processed = 0
    last: dict[str, Any] = {}
    while True:
        last = run_once(max_items=max_items)
        processed += int(last.get("processed") or 0)
        iterations += 1
        if stop_after and iterations >= stop_after:
            break
        sleep_fn(max(5, interval_seconds))
    return {"ok": True, "enabled": True, "iterations": iterations, "processed": processed, "last": last}


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Drain Hellion's deterministic Moltbook social queue.")
    parser.add_argument("--once", action="store_true", help="Drain one batch and exit even if the worker env is disabled.")
    parser.add_argument("--interval", type=int, default=int(os.getenv("CERBERUS_SOCIAL_WORKER_INTERVAL_SECONDS", "60")))
    parser.add_argument("--max-items", type=int, default=int(os.getenv("CERBERUS_SOCIAL_WORKER_MAX_ITEMS", "5")))
    args = parser.parse_args()
    if args.once:
        print(run_once(max_items=args.max_items))
        return 0
    print(run_loop(interval_seconds=args.interval, max_items=args.max_items))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
