"""Initialize and inspect the Cerberus long-term SQLite memory database."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from longterm_memory import LongTermMemoryStore  # noqa: E402


def main() -> int:
    store = LongTermMemoryStore()
    store.initialize()
    print(json.dumps(store.stats(), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
