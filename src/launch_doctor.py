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


def main() -> int:
    checks = readiness()
    checks["python"] = sys.version.split()[0]
    checks["sqlite3"] = sqlite3.sqlite_version
    checks["git_sha"] = os.getenv("RENDER_GIT_COMMIT", "")
    print(json.dumps(checks, ensure_ascii=True, indent=2))
    return 0 if checks.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
