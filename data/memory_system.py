"""
Compact local memory for Cerberus.

The agent should store small, typed strings instead of raw state dumps. This
keeps local memory easier to audit, cheaper to load, and less likely to leak
large prompt/log blobs into future decisions.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from secret_vault import SecretVaultError, read_vault, write_vault


MEMORY_VERSION = 1
_DEFAULT_MEMORY_ROOT = os.getenv("CERBERUS_MEMORY_DIR") or os.getenv("CERBERUS_HOME")
DEFAULT_MEMORY_DIR = Path(_DEFAULT_MEMORY_ROOT) if _DEFAULT_MEMORY_ROOT else Path.home() / ".cerberus"
DEFAULT_MEMORY_FILE = DEFAULT_MEMORY_DIR / "memory.compact.json"
DEFAULT_ENCRYPTED_FILE = DEFAULT_MEMORY_DIR / "memory.compact.vault.json"
DEFAULT_MAX_TURNS = 240
DEFAULT_MAX_SHORT_TURNS = 48
DEFAULT_MAX_SUMMARIES = 96
DEFAULT_MAX_LESSONS = 96
DEFAULT_MAX_FACTS = 128
DEFAULT_CONTEXT_BYTES = 6000
SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "mnemonic",
    "password",
    "privatekey",
    "private_key",
    "secret",
    "seed",
    "token",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def stable_hash(value: Any, *, length: int = 16) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()[:length]


def scrub_scalar(value: Any, *, limit: int = 160) -> str:
    if value is None:
        return ""
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = " ".join(text.split())
    if len(text) > limit:
        return text[: limit - 1] + "~"
    return text


def secret_like_key(key: str) -> bool:
    normalized = "".join(ch for ch in key.lower() if ch.isalnum() or ch == "_")
    return any(marker in normalized for marker in SECRET_KEYS)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if secret_like_key(str(key)):
                out[key] = f"[redacted:{stable_hash(item, length=10)}]"
            else:
                out[key] = redact_secrets(item)
        return out
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


def kv_string(prefix: str, pairs: Iterable[tuple[str, Any]]) -> str:
    body = []
    for key, value in pairs:
        text = scrub_scalar(value)
        if text != "":
            body.append(f"{key}={text}")
    return prefix + "|" + ";".join(body)


def parse_kv_body(body: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for part in body.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        if key:
            parsed[key] = value
    return parsed


def parse_compact_segments(line: str) -> dict[str, dict[str, str]]:
    segments: dict[str, dict[str, str]] = {}
    parts = str(line).split("|")
    for index in range(0, len(parts) - 1, 2):
        prefix = parts[index]
        body = parts[index + 1]
        if prefix not in {"T", "A", "O", "L", "S", "F"}:
            continue
        segments[prefix] = parse_kv_body(body)
    return segments


def clipped_join(values: Iterable[str], *, limit: int = 5) -> str:
    cleaned = []
    for value in values:
        text = scrub_scalar(value, limit=32)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return ",".join(cleaned)


def count_values(values: Iterable[str], *, limit: int = 6) -> str:
    counts: dict[str, int] = {}
    for value in values:
        text = scrub_scalar(value, limit=32) or "unknown"
        counts[text] = counts.get(text, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ",".join(f"{key}:{count}" for key, count in ranked)


def numeric_range(values: Iterable[str]) -> str:
    numbers = []
    for value in values:
        try:
            numbers.append(float(value))
        except (TypeError, ValueError):
            continue
    if not numbers:
        return ""
    low = min(numbers)
    high = max(numbers)
    if low.is_integer() and high.is_integer():
        return f"{int(low)}-{int(high)}"
    return f"{low:.2f}-{high:.2f}"


def summarize_turn_window(turns: list[str]) -> str:
    parsed = [parse_compact_segments(turn) for turn in turns]
    turn_segments = [item.get("T", {}) for item in parsed]
    action_segments = [item.get("A", {}) for item in parsed]
    outcome_segments = [item.get("O", {}) for item in parsed]
    return kv_string(
        "S",
        [
            ("ts", utc_now()),
            ("n", len(turns)),
            ("range", numeric_range(item.get("turn", "") for item in turn_segments)),
            ("regions", clipped_join(item.get("region", "") for item in turn_segments)),
            ("actions", count_values(item.get("type", "") for item in action_segments)),
            ("hp", numeric_range(item.get("hp", "") for item in turn_segments)),
            ("ep", numeric_range(item.get("ep", "") for item in turn_segments)),
            ("alert", numeric_range(item.get("alert", "") for item in turn_segments)),
            ("death", count_values(item.get("death", "") for item in turn_segments if item.get("death", ""))),
            ("ok", count_values(item.get("ok", "") for item in outcome_segments if item.get("ok", ""))),
            ("raw", stable_hash(turns, length=24)),
        ],
    )


def compact_turn_state(state: dict[str, Any]) -> str:
    safe = redact_secrets(state)
    view = safe.get("view", safe)
    self_state = view.get("self", {})
    region = view.get("currentRegion", {})
    pending_deathzones = view.get("pendingDeathzones", [])
    visible_agents = view.get("visibleAgents", [])
    visible_monsters = view.get("visibleMonsters", [])

    alert = (
        view.get("alertGauge")
        or self_state.get("alertGauge")
        or view.get("alert")
        or self_state.get("alert")
    )
    region_id = region.get("id") or view.get("regionId") or state.get("regionId")
    region_name = region.get("name") or view.get("regionName")

    return kv_string(
        "T",
        [
            ("ts", utc_now()),
            ("turn", state.get("turn") or view.get("turn")),
            ("hp", self_state.get("hp")),
            ("ep", self_state.get("ep")),
            ("atk", self_state.get("atk")),
            ("def", self_state.get("def")),
            ("alive", self_state.get("isAlive")),
            ("region", region_id),
            ("rname", region_name),
            ("death", region.get("isDeathZone")),
            ("pdz", len(pending_deathzones)),
            ("agents", len(visible_agents)),
            ("monsters", len(visible_monsters)),
            ("alert", alert),
            ("raw", stable_hash(safe)),
        ],
    )


def compact_lesson(domain: str, text: str, *, source: str = "", confidence: str = "") -> str:
    return kv_string(
        "L",
        [
            ("ts", utc_now()),
            ("dom", domain),
            ("conf", confidence),
            ("src", source),
            ("txt", text),
        ],
    )


class CompactMemoryStore:
    """Persist compact agent memory with integrity hashes and optional encryption."""

    def __init__(
        self,
        *,
        path: str | Path = DEFAULT_MEMORY_FILE,
        encrypted_path: str | Path = DEFAULT_ENCRYPTED_FILE,
        max_turns: int = DEFAULT_MAX_TURNS,
        max_short_turns: int | None = None,
        max_summaries: int = DEFAULT_MAX_SUMMARIES,
        max_lessons: int = DEFAULT_MAX_LESSONS,
        max_facts: int = DEFAULT_MAX_FACTS,
        context_bytes: int = DEFAULT_CONTEXT_BYTES,
    ):
        self.path = Path(path)
        self.encrypted_path = Path(encrypted_path)
        self.max_turns = max_turns
        self.max_short_turns = min(max_short_turns or DEFAULT_MAX_SHORT_TURNS, max_turns)
        self.max_summaries = max_summaries
        self.max_lessons = max_lessons
        self.max_facts = max_facts
        self.context_bytes = context_bytes
        self.data: dict[str, Any] = self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "type": "cerberus.compact_memory",
            "version": MEMORY_VERSION,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "profile": {},
            "facts": [],
            "summaries": [],
            "turns": [],
            "lessons": [],
            "source_hashes": {},
            "integrity": {},
        }

    def load(self) -> "CompactMemoryStore":
        try:
            if self.encrypted_path.exists():
                self.data = read_vault(self.encrypted_path)
            elif self.path.exists():
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            else:
                self.data = self._empty()
        except (OSError, json.JSONDecodeError, SecretVaultError) as exc:
            self.data = self._empty()
            self.data["load_warning"] = str(exc)[:240]
        self._verify_or_refresh()
        return self

    def save(self, *, encrypt: bool | None = None) -> Path:
        self.compact()
        self.data["updated_at"] = utc_now()
        self.data["integrity"] = self._integrity()

        should_encrypt = encrypt
        if should_encrypt is None:
            should_encrypt = bool(os.getenv("CERBERUS_PIN"))

        if should_encrypt:
            return write_vault(
                self.encrypted_path,
                self.data,
                purpose="cerberus.compact_memory",
            )

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        return self.path

    def remember_turn(
        self,
        state: dict[str, Any],
        *,
        action: dict[str, Any] | None = None,
        outcome: dict[str, Any] | None = None,
    ) -> str:
        turn = compact_turn_state(state)
        if action:
            turn += "|" + kv_string(
                "A",
                [
                    ("type", action.get("type")),
                    ("target", action.get("targetId") or action.get("regionId")),
                    ("why", action.get("reason") or action.get("thought")),
                ],
            )
        if outcome:
            turn += "|" + kv_string(
                "O",
                [
                    ("ok", outcome.get("success") or outcome.get("ok")),
                    ("code", outcome.get("code")),
                    ("hp", outcome.get("hp")),
                    ("ep", outcome.get("ep")),
                ],
            )
        self.data.setdefault("turns", []).append(turn)
        self.compact()
        return turn

    def remember_lesson(
        self,
        domain: str,
        text: str,
        *,
        source: str = "",
        confidence: str = "",
    ) -> str:
        lesson = compact_lesson(domain, text, source=source, confidence=confidence)
        existing = self.data.setdefault("lessons", [])
        if lesson not in existing:
            existing.append(lesson)
        self.data["lessons"] = existing[-self.max_lessons :]
        return lesson

    def remember_fact(self, fact: str) -> None:
        text = scrub_scalar(fact, limit=320)
        facts = self.data.setdefault("facts", [])
        if text and text not in facts:
            facts.append(text)
        self.data["facts"] = facts[-self.max_facts :]

    def set_profile(self, **values: Any) -> None:
        profile = self.data.setdefault("profile", {})
        for key, value in values.items():
            if secret_like_key(key):
                continue
            profile[key] = scrub_scalar(value, limit=120)

    def source_hash(self, name: str, value: Any) -> str:
        digest = stable_hash(value, length=24)
        self.data.setdefault("source_hashes", {})[name] = digest
        return digest

    def agent_context(self) -> str:
        sections: list[tuple[str, list[str]]] = []
        profile = self.data.get("profile", {})
        if isinstance(profile, dict) and profile:
            sections.append(("[profile]", [kv_string("P", sorted(profile.items()))]))
        for key in ("facts", "lessons", "summaries", "turns"):
            values = self.data.get(key, [])
            if values:
                sections.append((f"[{key}]", [str(item) for item in values]))
        return self._bounded_context(sections)

    def compact(self) -> None:
        self.data.setdefault("facts", [])
        self.data.setdefault("lessons", [])
        self.data.setdefault("summaries", [])
        self.data.setdefault("turns", [])
        self._rollup_old_turns()
        self.data["facts"] = self._dedupe_tail(self.data.get("facts", []), self.max_facts)
        self.data["lessons"] = self._dedupe_tail(self.data.get("lessons", []), self.max_lessons)
        self.data["summaries"] = self._dedupe_tail(
            self.data.get("summaries", []),
            self.max_summaries,
        )
        self.data["turns"] = self._dedupe_tail(self.data.get("turns", []), self.max_short_turns)

    def rewrite(self, *, encrypt: bool | None = None) -> Path:
        self.compact()
        return self.save(encrypt=encrypt)

    def _integrity(self) -> dict[str, Any]:
        body = {
            key: value
            for key, value in self.data.items()
            if key != "integrity"
        }
        return {
            "sha256": stable_hash(body, length=64),
            "counts": {
                "facts": len(self.data.get("facts", [])),
                "turns": len(self.data.get("turns", [])),
                "lessons": len(self.data.get("lessons", [])),
                "summaries": len(self.data.get("summaries", [])),
            },
        }

    def _verify_or_refresh(self) -> None:
        stored = self.data.get("integrity", {}).get("sha256")
        current = self._integrity()["sha256"]
        if stored and stored != current:
            self.data.setdefault("integrity", {})["warning"] = "integrity_changed"
        self.compact()
        self.data["integrity"] = self._integrity()

    def _rollup_old_turns(self) -> None:
        turns = self.data.get("turns", [])
        if not isinstance(turns, list):
            self.data["turns"] = []
            return
        if len(turns) <= self.max_short_turns:
            return
        overflow = [str(item) for item in turns[: -self.max_short_turns]]
        recent = turns[-self.max_short_turns :]
        summaries = self.data.setdefault("summaries", [])
        for index in range(0, len(overflow), self.max_short_turns):
            window = overflow[index : index + self.max_short_turns]
            if window:
                summaries.append(summarize_turn_window(window))
        self.data["turns"] = recent

    def _bounded_context(self, sections: list[tuple[str, list[str]]]) -> str:
        if self.context_bytes <= 0:
            return ""
        output: list[str] = []
        used = 0
        for header, values in sections:
            ordered = values if header in {"[profile]", "[facts]"} else list(reversed(values))
            block: list[str] = []
            for value in ordered:
                line = scrub_scalar(value, limit=420)
                if not line:
                    continue
                cost = len(line.encode("utf-8")) + 1
                if used + len(header) + 1 + cost > self.context_bytes:
                    break
                block.append(line)
                used += cost
            if block:
                output.append(header)
                output.extend(reversed(block) if header not in {"[profile]", "[facts]"} else block)
                used += len(header) + 1
            if used >= self.context_bytes:
                break
        return "\n".join(output)

    @staticmethod
    def _dedupe_tail(values: Any, limit: int) -> list[Any]:
        if not isinstance(values, list):
            return []
        deduped: list[Any] = []
        seen: set[str] = set()
        for item in reversed(values):
            marker = stable_hash(item, length=24)
            if marker in seen:
                continue
            seen.add(marker)
            deduped.append(item)
            if len(deduped) >= limit:
                break
        deduped.reverse()
        return deduped
