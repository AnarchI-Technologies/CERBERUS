"""Start CERBERUS with default-deny authentication on every non-health route."""

from __future__ import annotations

import hmac
import os
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse


def install_route_guards(module: Any) -> None:
    handler = module.CerberusHandler
    original_get: Callable[..., None] = handler.do_GET
    original_post: Callable[..., None] = handler.do_POST

    def authorized(instance: Any) -> bool:
        token = os.getenv("CERBERUS_HTTP_TOKEN", "").strip()
        if not token:
            return False
        provided = str(instance.headers.get("Authorization", ""))
        return hmac.compare_digest(provided, f"Bearer {token}")

    def guarded_get(instance: Any) -> None:
        path = urlparse(instance.path).path
        if path != "/healthz" and not authorized(instance):
            instance._send({"ok": False, "error": "unauthorized"}, status=401)
            return
        original_get(instance)

    def guarded_post(instance: Any) -> None:
        if not authorized(instance):
            instance._send({"ok": False, "error": "unauthorized"}, status=401)
            return
        original_post(instance)

    handler.do_GET = guarded_get
    handler.do_POST = guarded_post


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    for folder in (root / "src", root / "data"):
        value = str(folder)
        if value not in sys.path:
            sys.path.insert(0, value)

    if not os.getenv("CERBERUS_HTTP_TOKEN", "").strip():
        raise SystemExit("CERBERUS_HTTP_TOKEN is required")
    os.environ["CLAW_ROYALE_RUNTIME_ENABLED"] = "false"

    import render_app

    install_route_guards(render_app)
    return int(render_app.main())


if __name__ == "__main__":
    raise SystemExit(main())
