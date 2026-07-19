from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import claw_knowledge_sync


class FakeResponse:
    status_code = 200
    encoding = "utf-8"

    def __init__(self, url: str, content: bytes, content_type: str) -> None:
        self.url = url
        self.content = content
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        return None


class FakeSession:
    def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
        if url.endswith("/version"):
            return FakeResponse(url, b'{"version":"1.13.1"}', "application/json")
        return FakeResponse(url, b"<html><body><h1>Patch Note</h1><p>Safe update</p></body></html>", "text/html")


def test_sync_writes_official_provenance_and_readable_content() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "canonical.md"
        changed = claw_knowledge_sync.sync(output, FakeSession())
        text = output.read_text(encoding="utf-8")

    assert changed
    assert "1.13.1" in text
    assert "Patch Note" in text
    assert "SHA-256" in text
    assert "does not modify live policy automatically" in text

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "canonical.md"
        assert claw_knowledge_sync.sync(output, FakeSession())
        assert not claw_knowledge_sync.sync(output, FakeSession())


def test_rejects_redirect_outside_official_domain() -> None:
    class RedirectSession(FakeSession):
        def get(self, url: str, **kwargs):  # type: ignore[no-untyped-def]
            return FakeResponse("https://example.com/injected", b"bad", "text/plain")

    try:
        claw_knowledge_sync.fetch_canonical_sources(RedirectSession())
    except ValueError as exc:
        assert "redirect outside" in str(exc)
    else:
        raise AssertionError("unofficial redirect should be rejected")
