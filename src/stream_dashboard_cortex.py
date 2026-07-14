"""Public stream overlay cortex for Hellion.

This module turns internal runtime status into a sanitized, audience-safe stream
state. It deliberately excludes secrets, private thoughts, raw memory, wallet
keys, and deterministic decision chains.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote


PUBLIC_NAME = "Hellion"
BRAND_NAME = "AnarchI"
MAX_CHAT_MESSAGES = 50
MAX_CHAT_TEXT = 240
MAX_CHAT_AUTHOR = 32
MAX_VOICE_SOUNDBITES = 8
SENSITIVE_TERMS = re.compile(
    r"private key|seed phrase|mnemonic|api key|secret|token|bearer|"
    r"deterministic chain|decision chain|exact score|wallet secret|raw memory",
    re.I,
)
SAFE_GAME_ID = re.compile(r"[^A-Za-z0-9_-]")


def clean_public_text(value: Any, *, max_len: int = MAX_CHAT_TEXT) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    text = SENSITIVE_TERMS.sub("[private]", text)
    return text[:max_len]


def clean_game_id(value: Any) -> str:
    return SAFE_GAME_ID.sub("", clean_public_text(value, max_len=128))[:96]


def public_chat(messages: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    safe: list[dict[str, Any]] = []
    for item in list(messages or [])[-MAX_CHAT_MESSAGES:]:
        if not isinstance(item, dict):
            continue
        safe.append(
            {
                "author": clean_public_text(item.get("author", "viewer"), max_len=MAX_CHAT_AUTHOR) or "viewer",
                "message": clean_public_text(item.get("message", ""), max_len=MAX_CHAT_TEXT),
                "created_at": item.get("created_at", ""),
            }
        )
    return [item for item in safe if item["message"]]


def stream_mood(runtime: dict[str, Any]) -> str:
    state = str(runtime.get("state") or "").lower()
    error = str(runtime.get("last_error") or "").lower()
    if state in {"playing", "agent_view", "turn_advanced", "action_result", "can_act_changed"}:
        return "locked in"
    if "invalid_signature" in error:
        return "tuning the war drums"
    if state in {"queued", "assigned", "tx_submitted", "joined", "waiting", "signed_paid_join"}:
        return "entering the arena"
    if state in {"reconnecting", "connecting", "connected", "welcomed", "hello_sent"}:
        return "finding the gate"
    if state == "blocked":
        return "blocked but awake"
    return "standing by"


def stream_status_line(runtime: dict[str, Any], game_id: str) -> str:
    state = str(runtime.get("state") or "idle")
    if game_id and state in {"playing", "agent_view", "turn_advanced", "action_result", "can_act_changed"}:
        return f"Live game {game_id}"
    if state == "blocked":
        return "Launch check blocked"
    if state == "reconnecting":
        return "Reconnecting to Claw Royale"
    if state in {"queued", "assigned", "signed_paid_join", "tx_submitted", "joined"}:
        return "Paid-room entry in progress"
    return f"Runtime {state}"


@dataclass(slots=True)
class StreamDashboardCortex:
    spectate_base_url: str

    def public_state(
        self,
        *,
        runtime: dict[str, Any],
        current_game_id: str = "",
        chat: list[dict[str, Any]] | None = None,
        voice_lab: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        game_id = clean_game_id(current_game_id)
        runtime_state = str(runtime.get("state") or "idle")
        last_error = clean_public_text(runtime.get("last_error", ""), max_len=180)
        blockers = self.public_blockers(runtime=runtime, current_game_id=game_id)
        return {
            "ok": not blockers,
            "brand": BRAND_NAME,
            "host": PUBLIC_NAME,
            "mood": stream_mood(runtime),
            "status": stream_status_line(runtime, game_id),
            "thought": clean_public_text(runtime.get("last_public_thought", ""), max_len=180),
            "current_game_id": game_id,
            "spectate_url": self.spectate_url(game_id),
            "runtime": {
                "state": runtime_state,
                "mode": clean_public_text(runtime.get("mode", ""), max_len=24),
                "version": clean_public_text(runtime.get("version", ""), max_len=24),
                "last_frame_type": clean_public_text(runtime.get("last_frame_type", ""), max_len=48),
                "last_error": last_error,
            },
            "blockers": blockers,
            "stream": {
                "view_control": "hellion",
                "tip_label": "Hellion Tip Jar",
                "viewer_count": self.viewer_count_seed(runtime, game_id),
                "alerts": self.alerts(runtime),
            },
            "voice_lab": self.voice_lab_public(voice_lab or {}),
            "chat": public_chat(chat),
            "updated_at": int(time.time()),
        }

    def spectate_url(self, game_id: str) -> str:
        return f"{self.spectate_base_url.rstrip('/')}/{quote(game_id, safe='')}" if game_id else ""

    def public_blockers(self, *, runtime: dict[str, Any], current_game_id: str) -> list[str]:
        blockers: list[str] = []
        state = str(runtime.get("state") or "")
        error = str(runtime.get("last_error") or "")
        if not current_game_id:
            blockers.append("Waiting for a Claw Royale game ID")
        if state in {"blocked", "reconnecting"} and error:
            blockers.append(clean_public_text(error, max_len=180))
        return blockers[:4]

    def alerts(self, runtime: dict[str, Any]) -> list[dict[str, str]]:
        alerts: list[dict[str, str]] = []
        state = str(runtime.get("state") or "")
        if state == "signed_paid_join":
            alerts.append({"kind": "entry", "text": "Hellion signed the paid-room entry"})
        if state == "playing" or (state in {"agent_view", "turn_advanced"} and runtime.get("current_game_id")):
            alerts.append({"kind": "live", "text": "Hellion is in the arena"})
        if runtime.get("tx_hash"):
            alerts.append({"kind": "chain", "text": "Entry transaction submitted"})
        return alerts[-3:]

    def viewer_count_seed(self, runtime: dict[str, Any], game_id: str) -> int:
        seed = f"{game_id}:{runtime.get('state','')}:{runtime.get('last_frame_type','')}"
        return 7 + (sum(ord(char) for char in seed) % 38)

    def voice_lab_public(self, voice_lab: dict[str, Any]) -> dict[str, Any]:
        raw_items = voice_lab.get("soundbites") or voice_lab.get("lines") or voice_lab.get("clips") or []
        if not isinstance(raw_items, list):
            raw_items = []
        soundbites: list[dict[str, Any]] = []
        for item in raw_items[-MAX_VOICE_SOUNDBITES:]:
            if isinstance(item, str):
                text = item
                audio_url = ""
                mood = ""
            elif isinstance(item, dict):
                text = item.get("text") or item.get("line") or item.get("caption") or ""
                audio_url = item.get("audio_url") or item.get("url") or ""
                mood = item.get("mood") or item.get("tag") or ""
            else:
                continue
            cleaned = clean_public_text(text, max_len=220)
            if not cleaned:
                continue
            soundbites.append(
                {
                    "text": cleaned,
                    "mood": clean_public_text(mood, max_len=32),
                    "audio_url": clean_public_text(audio_url, max_len=300),
                }
            )
        return {
            "enabled": bool(soundbites),
            "source": clean_public_text(voice_lab.get("source", "hellion voice lab"), max_len=80),
            "updated_at": voice_lab.get("updated_at", ""),
            "soundbites": soundbites,
        }


def chat_message(author: Any, message: Any) -> dict[str, Any]:
    return {
        "author": clean_public_text(author, max_len=MAX_CHAT_AUTHOR) or "viewer",
        "message": clean_public_text(message, max_len=MAX_CHAT_TEXT),
        "created_at": int(time.time()),
    }
