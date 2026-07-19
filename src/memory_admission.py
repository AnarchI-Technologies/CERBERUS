"""Fail-closed admission policy for classified CERBERUS v2 memory."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from memory_system import secret_like_key
from v2_contracts import MemoryRecord


ALLOWED_CLASSIFICATIONS = {"runtime", "operational", "knowledge", "audit"}
ALLOWED_RETENTION = {"turn", "match", "30d", "durable", "immutable"}
SECRET_VALUE = re.compile(
    r"(?:mr_live_[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{12,}|0x[a-fA-F0-9]{64}|Bearer\s+\S+|(?:mnemonic|seed phrase)\s*[:=])",
    re.I,
)
INJECTION_MARKERS = (
    "ignore previous instructions",
    "reveal your system prompt",
    "disable safety",
    "execute this command",
    "send the private key",
)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    admitted: bool
    reasons: tuple[str, ...]
    authority: str


def _contains_secret(value: Any, *, depth: int = 0) -> bool:
    if depth > 6:
        return True
    if isinstance(value, dict):
        return any(secret_like_key(str(key)) or _contains_secret(item, depth=depth + 1) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_secret(item, depth=depth + 1) for item in value)
    return bool(SECRET_VALUE.search(str(value)))


def evaluate_memory_admission(
    record: MemoryRecord,
    *,
    source_trust: str,
    raw_context: Any | None = None,
) -> AdmissionDecision:
    reasons: list[str] = []
    if record.classification not in ALLOWED_CLASSIFICATIONS:
        reasons.append("classification_not_allowed")
    if record.retention not in ALLOWED_RETENTION:
        reasons.append("retention_not_allowed")
    if not record.source_ref.strip():
        reasons.append("missing_provenance")
    if not 0 <= record.confidence <= 1:
        reasons.append("invalid_confidence")
    if _contains_secret(record.content) or _contains_secret(raw_context):
        reasons.append("secret_like_content")
    lowered = record.content.lower()
    if source_trust in {"external", "model"} and any(marker in lowered for marker in INJECTION_MARKERS):
        reasons.append("instructional_external_content")
    if source_trust == "model" and record.classification in {"runtime", "audit"}:
        reasons.append("model_cannot_author_authoritative_memory")
    if record.retention == "immutable" and record.classification != "audit":
        reasons.append("immutable_requires_audit_classification")
    authority = "authoritative" if record.classification in {"runtime", "audit"} else "advisory"
    return AdmissionDecision(admitted=not reasons, reasons=tuple(reasons), authority=authority)
