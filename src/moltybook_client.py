"""
Thin MoltyBook client facade.

This keeps network posting separate from Social Cortex so drafts can be reviewed,
queued, rate-limited, or disabled without changing brain logic.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_MOLTYBOOK_API = "https://api.moltbook.com/v1"


@dataclass(slots=True)
class MoltyBookClient:
    api_key: str = ""
    base_url: str = DEFAULT_MOLTYBOOK_API
    enabled: bool = False

    @classmethod
    def from_env(cls) -> "MoltyBookClient":
        key = os.getenv("MOLTYBOOK_API_KEY") or os.getenv("MOLTBOOK_API_KEY") or ""
        enabled = os.getenv("CERBERUS_MOLTYBOOK_ENABLED", "false").lower() == "true"
        return cls(api_key=key, enabled=enabled)

    def post_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "moltybook disabled", "draft": draft}
        if not self.api_key:
            return {"ok": False, "skipped": True, "reason": "missing MoltyBook API key", "draft": draft}

        body = {
            "content": draft.get("content", "")[:500],
            "category": draft.get("category", "cerberus_update"),
        }
        if draft.get("submolt"):
            body["submolt"] = draft["submolt"]
        if draft.get("targetAgentId"):
            body["targetAgentId"] = draft["targetAgentId"]

        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/posts",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = response.read().decode("utf-8", errors="replace")
                return {"ok": 200 <= response.status < 300, "status": response.status, "body": payload}
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return {"ok": False, "skipped": False, "reason": "moltybook post failed", "error": str(exc)[:240], "draft": draft}

    def follow(self, effect: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "skipped": True, "reason": "moltybook disabled", "effect": effect}
        if not self.api_key:
            return {"ok": False, "skipped": True, "reason": "missing MoltyBook API key", "effect": effect}

        handle = effect.get("handle")
        if not handle:
            return {"ok": False, "skipped": True, "reason": "missing handle", "effect": effect}

        req = urllib.request.Request(
            f"{self.base_url.rstrip('/')}/follows",
            data=json.dumps({"handle": handle}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "X-API-Key": self.api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                payload = response.read().decode("utf-8", errors="replace")
                return {"ok": 200 <= response.status < 300, "status": response.status, "body": payload}
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return {"ok": False, "skipped": False, "reason": "moltybook follow failed", "error": str(exc)[:240], "effect": effect}


def process_social_side_effects(
    effects: list[dict[str, Any]],
    *,
    client: MoltyBookClient | None = None,
) -> list[dict[str, Any]]:
    mb = client or MoltyBookClient.from_env()
    results = []
    for effect in effects:
        effect_type = effect.get("type")
        if effect_type == "moltybook_draft":
            results.append(mb.post_draft(effect))
        elif effect_type == "moltybook_follow":
            results.append(mb.follow(effect))
        else:
            results.append({"ok": False, "skipped": True, "reason": "unsupported side effect", "effect": effect})
    return results
