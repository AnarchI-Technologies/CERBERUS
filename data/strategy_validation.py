"""
Validation scan for strategies learned through discussion.

This is deliberately conservative: social claims are untrusted until they pass
basic rule compatibility and pathing checks.
"""

from __future__ import annotations

from dataclasses import dataclass

from knowledge_base import KnowledgeBase


@dataclass(slots=True)
class StrategyValidation:
    accepted: bool
    confidence: float
    reason: str
    compact_note: str


class StrategyValidator:
    def __init__(self, knowledge: KnowledgeBase | None = None):
        self.knowledge = knowledge or KnowledgeBase().load()

    def validate(self, text: str) -> StrategyValidation:
        normalized = " ".join(text.lower().split())
        score = 0.0
        reasons: list[str] = []

        if not normalized or len(normalized) < 12:
            return StrategyValidation(False, 0.0, "too short", "")

        if any(term in normalized for term in ("private key", "api key", "seed phrase", "mnemonic")):
            return StrategyValidation(False, 0.0, "contains secret-seeking language", "")

        if "explore" in normalized and "alert" not in normalized:
            score -= 0.25
            reasons.append("mentions explore without alert cost")
        if "guardian" in normalized and "alert" in normalized:
            score += 0.25
            reasons.append("guardian claim aligns with Alert model")
        if "death" in normalized or "zone" in normalized:
            score += 0.2
            reasons.append("accounts for death-zone pressure")
        if "relic" in normalized or "pack" in normalized or "ruin" in normalized:
            score += 0.25
            reasons.append("accounts for Pre-S1 progression")
        if "always" in normalized or "never" in normalized:
            score -= 0.15
            reasons.append("absolute language requires caution")
        if "30" in normalized and "cooldown" in normalized:
            score += 0.1
            reasons.append("matches current cooldown")
        if "60" in normalized and "cooldown" in normalized:
            score -= 0.25
            reasons.append("likely stale cooldown")

        confidence = max(0.0, min(1.0, 0.45 + score))
        accepted = confidence >= 0.58
        compact = f"S|conf={confidence:.2f};txt={text[:180]}" if accepted else ""
        return StrategyValidation(
            accepted=accepted,
            confidence=confidence,
            reason="; ".join(reasons) or "generic plausible but weak evidence",
            compact_note=compact,
        )
