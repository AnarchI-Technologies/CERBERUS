"""Provenance-bound local retrieval over admitted official knowledge."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claw_knowledge_sync import fetch_canonical_sources
from memory_admission import evaluate_memory_admission
from model_gateway import OllamaModelGateway
from v2_contracts import MemoryRecord


def admitted_chunks(
    records: list[dict[str, str | int]], *, indexed_at: str, max_chars: int = 1200
) -> list[dict[str, str]]:
    chunks: list[dict[str, str]] = []
    for record in records:
        url = str(record["url"])
        paragraphs = [part.strip() for part in str(record["content"]).split("\n\n") if part.strip()]
        for index, paragraph in enumerate(paragraphs):
            for offset in range(0, len(paragraph), max_chars):
                content = paragraph[offset : offset + max_chars]
                source_id = hashlib.sha256(
                    f"{url}|{record['sha256']}|{index}|{offset}|{content}".encode("utf-8")
                ).hexdigest()[:24]
                decision = evaluate_memory_admission(
                    MemoryRecord(
                        record_id=source_id,
                        classification="knowledge",
                        source_ref=f"official:{url}#{record['sha256']}",
                        recorded_at=indexed_at,
                        content=content,
                        retention="durable",
                        confidence=1.0,
                    ),
                    source_trust="official",
                    raw_context={"url": url, "status": record["status"], "sha256": record["sha256"]},
                )
                if not decision.admitted:
                    continue
                chunks.append(
                    {
                        "source_id": source_id,
                        "source_url": url,
                        "source_sha256": str(record["sha256"]),
                        "indexed_at": indexed_at,
                        "content": content,
                    }
                )
    return chunks


def build_index(
    records: list[dict[str, str | int]],
    *,
    gateway: OllamaModelGateway,
    alias: str = "cerberus-embed",
    indexed_at: str | None = None,
) -> dict[str, Any]:
    stamp = indexed_at or datetime.now(timezone.utc).isoformat()
    chunks = admitted_chunks(records, indexed_at=stamp)
    output_rows: list[dict[str, Any]] = []
    for start in range(0, len(chunks), 32):
        batch = chunks[start : start + 32]
        embedded = gateway.embed(
            alias=alias,
            texts=[f"search_document: {item['content']}" for item in batch],
            allow_evaluation=True,
        )
        for item, vector in zip(batch, embedded.vectors, strict=True):
            output_rows.append({**item, "vector": list(vector)})
    return {
        "schema_version": "cerberus.knowledge_index.v1",
        "indexed_at": stamp,
        "alias": alias,
        "model": embedded.model if chunks else "",
        "digest": embedded.digest if chunks else "",
        "record_count": len(output_rows),
        "records": output_rows,
    }


def _cosine(left: list[float], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return -1.0
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    denominator = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return numerator / denominator if denominator else -1.0


def query_index(
    index: dict[str, Any],
    query: str,
    *,
    gateway: OllamaModelGateway,
    limit: int = 5,
) -> list[dict[str, Any]]:
    alias = str(index.get("alias") or "cerberus-embed")
    embedded = gateway.embed(alias=alias, texts=[f"search_query: {query}"], allow_evaluation=True)
    vector = embedded.vectors[0]
    ranked: list[dict[str, Any]] = []
    for item in index.get("records", []):
        if not isinstance(item, dict) or not isinstance(item.get("vector"), list):
            continue
        score = _cosine([float(value) for value in item["vector"]], vector)
        ranked.append(
            {
                "source_id": item.get("source_id"),
                "source_url": item.get("source_url"),
                "source_sha256": item.get("source_sha256"),
                "indexed_at": item.get("indexed_at"),
                "score": score,
                "content": str(item.get("content") or "")[:1200],
            }
        )
    return sorted(ranked, key=lambda item: (-item["score"], str(item["source_id"])))[: max(1, min(limit, 20))]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/var/data/.cerberus/official_knowledge_index.json"))
    args = parser.parse_args()
    index = build_index(fetch_canonical_sources(), gateway=OllamaModelGateway())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(index, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"record_count": index["record_count"], "indexed_at": index["indexed_at"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
