"""
Compact knowledge loader for Cerberus.
"""

from __future__ import annotations

from pathlib import Path

from knowledge_compactor import DEFAULT_OUT, load_compact_knowledge


class KnowledgeBase:
    def __init__(self, path: str | Path = DEFAULT_OUT):
        self.path = Path(path)
        self.data = {"k": []}
        self.facts: list[str] = []
        self.by_domain: dict[str, list[str]] = {}

    def load(self) -> "KnowledgeBase":
        try:
            if self.path.exists():
                self.data = load_compact_knowledge(self.path)
                self.facts = list(self.data.get("k", []))
            else:
                self.data = {"k": []}
                self.facts = []
        except Exception as exc:
            self.data = {"k": [], "load_warning": str(exc)[:240]}
            self.facts = []
        self.by_domain = {}
        for fact in self.facts:
            domain = self._domain_for(fact)
            self.by_domain.setdefault(domain, []).append(fact)
        return self

    def find(self, *terms: str, limit: int = 12) -> list[str]:
        lowered = [term.lower() for term in terms if term]
        hits = [
            fact
            for fact in self.facts
            if all(term in fact.lower() for term in lowered)
        ]
        return hits[:limit]

    def domain(self, name: str, limit: int = 20) -> list[str]:
        return self.by_domain.get(name, [])[:limit]

    def _domain_for(self, fact: str) -> str:
        if fact.startswith("F|"):
            parts = fact.split("|", 2)
            if len(parts) > 1:
                return parts[1]
        if fact.startswith("M|"):
            parts = fact.split("|", 4)
            if len(parts) > 2:
                return f"markdown.{parts[2]}"
        return "unknown"
