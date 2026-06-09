"""Local import bootstrap for the Cerberus workspace.

Python imports this module automatically when running from the repository root.
It keeps the current flat ``src``/``data`` layout usable without requiring every
probe, script, or test runner to hand-author ``PYTHONPATH``.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if folder.exists() and path not in sys.path:
        sys.path.insert(0, path)
