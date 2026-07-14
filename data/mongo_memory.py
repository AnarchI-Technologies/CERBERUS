"""Mongo-backed long-term memory for Cerberus.

Mongo is used as a durable shared memory backend when configured, while the
runtime can still fall back to the local SQLite store if Atlas is unavailable.
"""

from __future__ import annotations

import os
from typing import Any

from longterm_memory import LongTermMemoryStore
from memory_system import stable_hash, utc_now


MONGO_BACKEND_VALUES = {"mongo", "mongodb", "atlas"}


def mongo_backend_enabled() -> bool:
    return os.getenv("CERBERUS_MEMORY_BACKEND", "").strip().lower() in MONGO_BACKEND_VALUES


def mongo_uri() -> str:
    return (
        os.getenv("CERBERUS_MONGODB_URI")
        or os.getenv("MONGODB_URI")
        or ""
    ).strip()


def mongo_database_name() -> str:
    return (
        os.getenv("CERBERUS_MONGODB_DATABASE")
        or os.getenv("CERBERUS_MONGO_DATABASE")
        or os.getenv("MONGODB_DATABASE")
        or "cerberus"
    ).strip() or "cerberus"


def mongo_collection_prefix() -> str:
    raw = (
        os.getenv("CERBERUS_MONGODB_COLLECTION_PREFIX")
        or os.getenv("CERBERUS_MONGO_COLLECTION_PREFIX")
        or "hellion"
    )
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw.strip().lower())
    return cleaned.strip("_") or "hellion"


def _collection_name(name: str) -> str:
    return f"{mongo_collection_prefix()}_{name}"


class MongoLongTermMemoryStore:
    def __init__(
        self,
        *,
        uri: str | None = None,
        database: str | None = None,
        client: Any | None = None,
    ):
        self.uri = (uri if uri is not None else mongo_uri()).strip()
        self.database_name = (database or mongo_database_name()).strip() or "cerberus"
        self._client = client
        self._database: Any | None = None

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.uri:
            raise RuntimeError("MONGODB_URI is not configured")
        try:
            from pymongo import MongoClient
        except Exception as exc:
            raise RuntimeError("pymongo is not installed") from exc
        timeout_ms = int(os.getenv("CERBERUS_MONGO_WRITE_TIMEOUT_MS", "5000") or "5000")
        self._client = MongoClient(self.uri, serverSelectionTimeoutMS=timeout_ms)
        return self._client

    @property
    def db(self) -> Any:
        if self._database is None:
            self._database = self.client()[self.database_name]
        return self._database

    @property
    def memory_items(self) -> Any:
        return self.db[_collection_name("memory_items")]

    def initialize(self) -> str:
        collection = self.memory_items
        collection.create_index(
            [("kind", 1), ("scope", 1), ("key", 1), ("source_hash", 1)],
            unique=True,
            name="memory_identity",
        )
        collection.create_index(
            [("kind", 1), ("scope", 1), ("importance", -1), ("confidence", -1), ("last_seen", -1)],
            name="memory_lookup",
        )
        return f"mongodb://{self.database_name}/{collection.name}"

    def remember(
        self,
        *,
        kind: str,
        scope: str,
        key: str,
        text: str,
        confidence: float = 0.5,
        importance: int = 50,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        self.initialize()
        now = utc_now()
        clean_text = " ".join(str(text).replace("\r", " ").replace("\n", " ").split())[:500]
        source_hash = stable_hash(
            {"kind": kind, "scope": scope, "key": key, "text": clean_text},
            length=24,
        )
        item_id = stable_hash({"kind": kind, "scope": scope, "key": key, "source_hash": source_hash}, length=32)
        self.memory_items.update_one(
            {"kind": kind, "scope": scope, "key": key, "source_hash": source_hash},
            {
                "$setOnInsert": {
                    "_id": item_id,
                    "kind": kind,
                    "scope": scope,
                    "key": key,
                    "text": clean_text,
                    "first_seen": now,
                    "source_hash": source_hash,
                },
                "$set": {
                    "last_seen": now,
                    "metadata": metadata or {},
                    "confidence": max(0.0, min(1.0, confidence)),
                    "importance": max(0, min(100, importance)),
                },
                "$inc": {"hits": 1},
            },
            upsert=True,
        )
        return item_id

    def top(self, *, kind: str = "", scope: str = "", limit: int = 12) -> list[dict[str, Any]]:
        self.initialize()
        query: dict[str, Any] = {}
        if kind:
            query["kind"] = kind
        if scope:
            query["scope"] = scope
        rows = self.memory_items.find(query).sort(
            [("importance", -1), ("confidence", -1), ("hits", -1), ("last_seen", -1)]
        ).limit(max(1, limit))
        return [
            {
                "id": str(row.get("_id", "")),
                "kind": row.get("kind", ""),
                "scope": row.get("scope", ""),
                "key": row.get("key", ""),
                "text": row.get("text", ""),
                "confidence": row.get("confidence", 0.0),
                "importance": row.get("importance", 0),
                "hits": row.get("hits", 0),
                "last_seen": row.get("last_seen", ""),
            }
            for row in rows
        ]

    def compact_context(self, *, kind: str = "", scope: str = "", limit: int = 12) -> str:
        return "\n".join(row["text"] for row in self.top(kind=kind, scope=scope, limit=limit))

    def stats(self) -> dict[str, Any]:
        self.initialize()
        try:
            self.client().admin.command("ping")
        except Exception:
            pass
        return {
            "backend": "mongo",
            "database": self.database_name,
            "collection": self.memory_items.name,
            "items": self.memory_items.count_documents({}),
        }


class ResilientLongTermMemoryStore:
    """Prefer Mongo, but keep SQLite as a local fallback if Mongo fails."""

    def __init__(self, primary: Any, fallback: LongTermMemoryStore | None = None):
        self.primary = primary
        self.fallback = fallback or LongTermMemoryStore()
        self.last_primary_error = ""

    def initialize(self) -> Any:
        try:
            return self.primary.initialize()
        except Exception as exc:
            self.last_primary_error = str(exc)[:240]
            return self.fallback.initialize()

    def remember(self, **kwargs: Any) -> str:
        try:
            return self.primary.remember(**kwargs)
        except Exception as exc:
            self.last_primary_error = str(exc)[:240]
            return self.fallback.remember(**kwargs)

    def top(self, **kwargs: Any) -> list[dict[str, Any]]:
        try:
            return self.primary.top(**kwargs)
        except Exception as exc:
            self.last_primary_error = str(exc)[:240]
            return self.fallback.top(**kwargs)

    def compact_context(self, **kwargs: Any) -> str:
        return "\n".join(row["text"] for row in self.top(**kwargs))

    def stats(self) -> dict[str, Any]:
        try:
            stats = self.primary.stats()
            stats["fallback"] = "sqlite"
            stats["fallback_active"] = False
            return stats
        except Exception as exc:
            self.last_primary_error = str(exc)[:240]
            stats = self.fallback.stats()
            stats["backend"] = "sqlite"
            stats["fallback_from"] = "mongo"
            stats["fallback_active"] = True
            stats["mongo_error"] = self.last_primary_error
            return stats


def configured_longterm_memory_store() -> Any:
    if mongo_backend_enabled():
        return ResilientLongTermMemoryStore(MongoLongTermMemoryStore())
    return LongTermMemoryStore()
