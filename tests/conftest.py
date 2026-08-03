"""Keep canonical source roots ahead of legacy workspace facades during pytest collection."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "data", ROOT / "src"):
    path = str(folder)
    if path in sys.path:
        sys.path.remove(path)
    sys.path.insert(0, path)

for module_name, module in tuple(sys.modules.items()):
    module_file = getattr(module, "__file__", None)
    if not module_file:
        continue
    path = Path(module_file).resolve()
    if path.parent != ROOT:
        continue
    if (ROOT / "src" / path.name).is_file() or (ROOT / "data" / path.name).is_file():
        sys.modules.pop(module_name, None)
