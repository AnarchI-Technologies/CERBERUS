"""Repository invariant checks before hardened strategy is accepted."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from external_wisdom import invariant_guard_policy


ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _check(name: str, ok: bool, detail: str) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def repo_invariant_report(payload: dict[str, Any] | None = None, *, root: str | Path = ROOT) -> dict[str, Any]:
    base = Path(root)
    policy = invariant_guard_policy()
    checks: list[dict[str, Any]] = []
    required_files = [str(item) for item in policy.get("required_files", []) if str(item)]
    required_routes = [str(item) for item in policy.get("required_routes", []) if str(item)]
    required_effects = [str(item) for item in policy.get("required_side_effects", []) if str(item)]

    missing_files = [item for item in required_files if not (base / item).exists()]
    checks.append(_check("required_files", not missing_files, ",".join(missing_files) or "all present"))

    render_text = _read(base / "src" / "render_app.py")
    missing_routes = [item for item in required_routes if item not in render_text]
    checks.append(_check("dashboard_routes", not missing_routes, ",".join(missing_routes) or "all present"))

    claw_text = _read(base / "src" / "claw_runtime.py")
    websocket_ok = "websockets.connect(" in claw_text and "run_postgame_hardening_pass(" in claw_text
    checks.append(_check("runtime_websocket_and_postgame", websocket_ok, "websocket+postgame hooks"))

    social_text = _read(base / "src" / "social_runtime.py") + "\n" + _read(base / "src" / "social_cortex.py")
    missing_effects = [item for item in required_effects if item not in social_text]
    checks.append(_check("social_side_effects", not missing_effects, ",".join(missing_effects) or "all present"))

    map_text = _read(base / "src" / "game_map.py")
    topology_ok = "topology_verification" in map_text
    checks.append(_check("topology_verifier_hook", topology_ok, "map payload publishes topology verification"))

    if payload is not None:
        from hardened_strategy import sanity_check_rules

        rule_check = sanity_check_rules(payload)
        checks.append(_check("rules_sanity", bool(rule_check.get("ok")), str(rule_check.get("error") or "ok")))

    failures = [item for item in checks if not item["ok"]]
    return {
        "ok": not failures,
        "checks": checks,
        "failures": failures,
        "required_files": required_files,
        "required_routes": required_routes,
        "required_side_effects": required_effects,
    }
