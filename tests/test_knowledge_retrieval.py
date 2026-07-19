from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    sys.path.insert(0, str(folder))

from knowledge_retrieval import build_index, query_index


class FakeGateway:
    def embed(self, *, alias, texts, allow_evaluation):  # type: ignore[no-untyped-def]
        vectors = []
        for text in texts:
            lowered = text.lower()
            vectors.append((1.0 if "relic" in lowered else 0.0, 1.0 if "combat" in lowered else 0.0))
        return SimpleNamespace(vectors=tuple(vectors), model="local:embed", digest="pinned")


def test_index_excludes_flagged_source_and_preserves_provenance() -> None:
    records = [
        {
            "url": "https://www.clawroyale.ai/game-guide",
            "status": 200,
            "sha256": "a" * 64,
            "content": "Relic ruins can be explored.\n\nCombat costs energy.",
        },
        {
            "url": "https://www.clawroyale.ai/skill.md",
            "status": 200,
            "sha256": "b" * 64,
            "content": "Bearer never-index-this",
        },
    ]
    index = build_index(records, gateway=FakeGateway(), indexed_at="2026-07-19T00:00:00Z")

    assert index["record_count"] == 2
    assert {row["source_url"] for row in index["records"]} == {"https://www.clawroyale.ai/game-guide"}
    assert all(row["source_sha256"] == "a" * 64 for row in index["records"])
    assert all(row["indexed_at"] == "2026-07-19T00:00:00Z" for row in index["records"])


def test_chunk_admission_keeps_safe_sections_from_partly_flagged_official_source() -> None:
    records = [
        {
            "url": "https://www.clawroyale.ai/skill.md",
            "status": 200,
            "sha256": "b" * 64,
            "content": "Relic exploration is safe guidance.\n\nBearer never-index-this",
        }
    ]
    index = build_index(records, gateway=FakeGateway(), indexed_at="2026-07-19T00:00:00Z")

    assert index["record_count"] == 1
    assert index["records"][0]["content"] == "Relic exploration is safe guidance."


def test_query_returns_deterministic_scored_sources_with_freshness() -> None:
    records = [
        {
            "url": "https://www.clawroyale.ai/game-guide",
            "status": 200,
            "sha256": "a" * 64,
            "content": "Relic exploration rules.\n\nCombat targeting rules.",
        }
    ]
    index = build_index(records, gateway=FakeGateway(), indexed_at="2026-07-19T00:00:00Z")
    results = query_index(index, "find relics", gateway=FakeGateway(), limit=1)

    assert len(results) == 1
    assert results[0]["content"] == "Relic exploration rules."
    assert results[0]["source_id"]
    assert results[0]["indexed_at"] == "2026-07-19T00:00:00Z"
