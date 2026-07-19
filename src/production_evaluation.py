"""Sanitized reliability evidence for the local CERBERUS production proof."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from memory_system import atomic_write_text


def _json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _integer(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _loopback_health(url: str, timeout: float = 3.0) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return False
    try:
        with urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read(4096).decode("utf-8"))
        return bool(isinstance(payload, dict) and payload.get("ok") is True)
    except Exception:
        return False


def collect(memory_root: Path, *, health_url: str = "http://127.0.0.1:10000/healthz") -> dict[str, Any]:
    runtime = _json(memory_root / "claw_runtime_status.json")
    audit = runtime.get("action_audit") if isinstance(runtime.get("action_audit"), list) else []
    policy_rows = _json(memory_root / "policy_shadow.json").get("records", [])
    postmortems = _json(memory_root / "action_postmortems.json").get("records", [])
    policy_rows = policy_rows if isinstance(policy_rows, list) else []
    postmortems = postmortems if isinstance(postmortems, list) else []
    sent = [row for row in audit if isinstance(row, dict) and row.get("kind") == "action_sent"]
    results = [row for row in audit if isinstance(row, dict) and row.get("kind") == "action_result"]
    duplicate_suppressed = [
        row for row in audit if isinstance(row, dict) and row.get("kind") == "action_duplicate_suppressed"
    ]
    accepted = [row for row in results if isinstance(row.get("outcome"), dict) and row["outcome"].get("ok") is True]
    cooldown_rejected = [
        row
        for row in results
        if isinstance(row.get("outcome"), dict)
        and str(row["outcome"].get("code") or "").upper() == "COOLDOWN_ACTIVE"
    ]
    categories = Counter(str(row.get("failure_category") or "unknown") for row in postmortems if isinstance(row, dict))
    policy_outcomes = Counter(
        str(row.get("policy", {}).get("outcome") or "unknown")
        for row in policy_rows
        if isinstance(row, dict) and isinstance(row.get("policy"), dict)
    )
    claims = runtime.get("preseason1_claims") if isinstance(runtime.get("preseason1_claims"), dict) else {}
    progress = claims.get("progress") if isinstance(claims.get("progress"), list) else []
    objective_gaps = [
        {
            "key": str(item.get("key") or "")[:80],
            "level": _integer(item.get("level")),
            "levels_to_five": max(0, 5 - _integer(item.get("level"))),
        }
        for item in progress
        if isinstance(item, dict) and item.get("key") and _integer(item.get("level")) < 5
    ]
    return {
        "schema_version": "cerberus.production_evaluation.v1",
        "recorded_at": int(time.time()),
        "health_ok": _loopback_health(health_url),
        "runtime_state": str(runtime.get("state") or "unknown")[:40],
        "runtime_status_age_seconds": max(0, int(time.time()) - int(runtime.get("updated_at") or 0)),
        "live_claw_version": str(runtime.get("live_version") or "")[:40],
        "terminal_game_quarantined": bool(runtime.get("terminal_game_id")),
        "preseason_points": _integer((claims.get("summary") or {}).get("totalPoints"))
        if isinstance(claims.get("summary"), dict)
        else 0,
        "objective_level_gaps": objective_gaps,
        "actions_sent_window": len(sent),
        "action_results_window": len(results),
        "accepted_results_window": len(accepted),
        "action_result_success_rate": len(accepted) / max(1, len(results)),
        "cooldown_rejections_window": len(cooldown_rejected),
        "duplicate_actions_suppressed_window": len(duplicate_suppressed),
        "policy_shadow_records": len(policy_rows),
        "policy_outcomes": dict(policy_outcomes),
        "postmortem_records": len(postmortems),
        "postmortem_categories": dict(categories),
        "policy_enforcement_active": any(bool(row.get("enforced")) for row in policy_rows if isinstance(row, dict)),
        "credentials_collected": False,
    }


def append_sample(report_file: Path, sample: dict[str, Any], *, limit: int = 1000) -> dict[str, Any]:
    payload = _json(report_file)
    samples = payload.get("samples") if isinstance(payload.get("samples"), list) else []
    samples.append(sample)
    samples = samples[-limit:]
    health_rate = sum(1 for item in samples if isinstance(item, dict) and item.get("health_ok")) / max(1, len(samples))
    output = {
        "schema_version": "cerberus.production_evaluation_series.v1",
        "sample_count": len(samples),
        "health_success_rate": health_rate,
        "first_recorded_at": samples[0].get("recorded_at") if samples else 0,
        "last_recorded_at": samples[-1].get("recorded_at") if samples else 0,
        "samples": samples,
    }
    atomic_write_text(report_file, json.dumps(output, indent=2, sort_keys=True, ensure_ascii=True))
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--memory-root", type=Path, default=Path("/var/lib/cerberus/memory"))
    parser.add_argument("--output", type=Path, default=Path("/var/lib/cerberus/evaluation/production.json"))
    parser.add_argument("--health-url", default="http://127.0.0.1:10000/healthz")
    args = parser.parse_args()
    series = append_sample(args.output, collect(args.memory_root, health_url=args.health_url))
    print(json.dumps({key: series[key] for key in ("sample_count", "health_success_rate", "last_recorded_at")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
