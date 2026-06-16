"""Render/local launch preflight for Cerberus."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from render_app import readiness  # noqa: E402
from runtime_state import claw_runtime_status_file, read_json, stored_game_id  # noqa: E402
from profit_simulator import simulate  # noqa: E402


def launch_report() -> dict:
    checks = readiness()
    runtime = read_json(claw_runtime_status_file())
    account = runtime.get("account", {}) if isinstance(runtime.get("account"), dict) else {}
    readiness_flags = account.get("readiness", {}) if isinstance(account.get("readiness"), dict) else {}
    live_map = runtime.get("live_map", {}) if isinstance(runtime.get("live_map"), dict) else {}
    blockers = []
    env = checks.get("env", {})
    for key in (
        "CERBERUS_PIN",
        "CLAW_ROYALE_API_KEY",
        "CLAW_ROYALE_ERC8004_ID",
        "CERBERUS_AGENT_EOA_PRIVATE_KEY",
        "CERBERUS_AGENT_EOA_ADDRESS",
    ):
        if env.get(key) is False:
            blockers.append(f"missing {key}")
    if not checks.get("memory_writable"):
        blockers.append(f"memory disk not writable: {checks.get('memory_error', '')}")
    if not stored_game_id() and not runtime.get("current_game_id"):
        blockers.append("no active game id heartbeat")
    if runtime.get("state") in {"blocked", "reconnecting", "stale_game_leave_failed"}:
        blockers.append(f"runtime {runtime.get('state')}: {runtime.get('last_error', '')}")
    paid_ready = bool(readiness_flags.get("paidReady"))
    if not paid_ready:
        blockers.append("paid readiness false or unknown")
    profit = simulate(games_per_day=int(runtime.get("games_completed_24h") or 61), target_per_day=1000)
    if not profit.get("target_met"):
        blockers.append(f"profit simulation below target by {profit.get('gap_smoltz_per_day')} sMoltz/day")
    return {
        "ok": not blockers,
        "blockers": blockers,
        "checks": checks,
        "runtime": {
            "state": runtime.get("state", "unknown"),
            "mode": runtime.get("mode", "unknown"),
            "current_game_id": runtime.get("current_game_id") or stored_game_id(),
            "last_error": runtime.get("last_error", ""),
            "paid_ready": paid_ready,
            "balance": account.get("balance", ""),
            "live_map_ok": bool(live_map.get("ok")),
            "live_map_heartbeat": live_map.get("heartbeat", ""),
        },
        "profit": profit,
    }


def main() -> int:
    report = launch_report()
    report["python"] = sys.version.split()[0]
    report["sqlite3"] = sqlite3.sqlite_version
    report["git_sha"] = os.getenv("RENDER_GIT_COMMIT", "")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
