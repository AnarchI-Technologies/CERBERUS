"""Workspace-root Cerberus turn-loop facade."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from core_loop import cerberus_tick  # noqa: E402


__all__ = ["cerberus_tick"]
