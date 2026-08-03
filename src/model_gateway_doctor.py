"""Safe local Ollama readiness check with deterministic fallback reporting."""

from __future__ import annotations

import json

from model_gateway import OllamaModelGateway


def main() -> int:
    report = OllamaModelGateway().readiness()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("ok") and report.get("aliases_ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
