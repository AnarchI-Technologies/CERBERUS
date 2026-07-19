"""Read-only synchronization of canonical public Claw Royale knowledge."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

from memory_admission import evaluate_memory_admission
from v2_contracts import MemoryRecord


DEFAULT_SOURCES = (
    "https://cdn.clawroyale.ai/api/version",
    "https://www.clawroyale.ai/skill.md",
    "https://cdn.clawroyale.ai/openapi.yaml",
    "https://www.clawroyale.ai/game-guide.md",
    "https://www.clawroyale.ai/references/changelog.md",
    "https://www.clawroyale.ai/references/combat-items.md",
    "https://www.clawroyale.ai/references/preseason1-quests.md",
    "https://cdn.clawroyale.ai/api/posts?page=1&limit=20&type=patch_note",
    "https://cdn.clawroyale.ai/api/pack-catalog",
)
MAX_SOURCE_BYTES = 2_000_000
MAX_RENDERED_CHARS = 200_000
SNAPSHOT_SCHEMA_VERSION = 3


def _official_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "clawroyale.ai" or host.endswith(".clawroyale.ai")


def _readable_body(response: requests.Response) -> str:
    raw = response.content[:MAX_SOURCE_BYTES]
    text = raw.decode(response.encoding or "utf-8", errors="replace")
    content_type = response.headers.get("Content-Type", "").lower()
    if "json" in content_type:
        try:
            text = json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            pass
    elif "html" in content_type or "<html" in text[:500].lower():
        text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
        text = re.sub(r"(?s)<[^>]+>", "\n", text)
        text = html.unescape(text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_RENDERED_CHARS]


def fetch_canonical_sources(session: requests.Session | None = None) -> list[dict[str, str | int]]:
    client = session or requests.Session()
    records: list[dict[str, str | int]] = []
    live_version = ""
    for url in DEFAULT_SOURCES:
        if not _official_url(url):
            raise ValueError(f"non-official source rejected: {url}")
        headers = {"User-Agent": "CERBERUS-canonical-sync/1"}
        parsed = urlparse(url)
        if parsed.hostname == "cdn.clawroyale.ai" and parsed.path.startswith("/api/") and not parsed.path.endswith("/version"):
            if not live_version:
                raise ValueError("official version must be discovered before versioned API sources")
            headers["X-Version"] = live_version
        response = client.get(url, timeout=20, allow_redirects=True, headers=headers)
        response.raise_for_status()
        final_url = str(response.url or url)
        if not _official_url(final_url):
            raise ValueError(f"redirect outside clawroyale.ai rejected: {final_url}")
        body = _readable_body(response)
        if parsed.path.endswith("/version"):
            try:
                version_payload = json.loads(response.content.decode(response.encoding or "utf-8"))
                live_version = str(version_payload.get("version") or "").strip()
            except (AttributeError, TypeError, ValueError, UnicodeDecodeError):
                live_version = ""
            if not live_version:
                raise ValueError("official version response did not contain a version")
        records.append(
            {
                "url": final_url,
                "status": response.status_code,
                "sha256": hashlib.sha256(response.content[:MAX_SOURCE_BYTES]).hexdigest(),
                "content": body,
            }
        )
    return records


def _record_set_hash(records: list[dict[str, str | int]]) -> str:
    evidence = "\n".join(f"{record['url']}|{record['sha256']}" for record in records)
    return hashlib.sha256(evidence.encode("utf-8")).hexdigest()


def memory_admission_shadow(records: list[dict[str, str | int]], *, recorded_at: str) -> dict[str, object]:
    decisions: list[dict[str, object]] = []
    for record in records:
        digest = str(record["sha256"])
        url = str(record["url"])
        decision = evaluate_memory_admission(
            MemoryRecord(
                record_id=f"claw-official-{digest[:20]}",
                classification="knowledge",
                source_ref=f"official:{url}#{digest}",
                recorded_at=recorded_at,
                content=str(record["content"]),
                retention="durable",
                confidence=1.0,
            ),
            source_trust="official",
            raw_context={"url": url, "status": record["status"], "sha256": digest},
        )
        decisions.append(
            {
                "url": url,
                "admitted": decision.admitted,
                "authority": decision.authority,
                "reasons": list(decision.reasons),
            }
        )
    return {
        "mode": "shadow",
        "admitted": sum(1 for item in decisions if item["admitted"]),
        "flagged": sum(1 for item in decisions if not item["admitted"]),
        "decisions": decisions,
    }


def render_snapshot(records: list[dict[str, str | int]], *, generated_at: str | None = None) -> str:
    stamp = generated_at or datetime.now(timezone.utc).isoformat()
    set_hash = _record_set_hash(records)
    admission = memory_admission_shadow(records, recorded_at=stamp)
    lines = [
        "# Claw Royale canonical server snapshot",
        "",
        f"Snapshot schema: `{SNAPSHOT_SCHEMA_VERSION}`",
        f"Generated: `{stamp}`",
        f"Canonical set SHA-256: `{set_hash}`",
        "",
        "> Generated from public, official clawroyale.ai sources using GET requests only. "
        "This is evidence for review; it does not modify live policy automatically.",
        "",
        f"Memory admission shadow: `{admission['admitted']}` admitted, `{admission['flagged']}` flagged.",
    ]
    for record in records:
        lines.extend(
            [
                "",
                f"## {record['url']}",
                "",
                f"- HTTP status: `{record['status']}`",
                f"- SHA-256: `{record['sha256']}`",
                "",
                "```text",
                str(record["content"]).replace("```", "` ` `"),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def sync(output: Path, session: requests.Session | None = None) -> bool:
    records = fetch_canonical_sources(session)
    previous = output.read_text(encoding="utf-8") if output.exists() else ""
    current_hash = f"Canonical set SHA-256: `{_record_set_hash(records)}`"
    current_schema = f"Snapshot schema: `{SNAPSHOT_SCHEMA_VERSION}`"
    if current_hash in previous and current_schema in previous:
        return False
    rendered = render_snapshot(records)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/claw_royale_canonical_snapshot.md"))
    args = parser.parse_args()
    changed = sync(args.output)
    print("updated" if changed else "unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
