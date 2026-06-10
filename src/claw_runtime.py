"""Always-on Claw Royale gameplay connector for Render.

The dashboard and /tick endpoint are only the shell. This worker owns the live
WebSocket loop: join/listen, pass snapshots through the deterministic cortex,
send actions back, and persist compact runtime status for the dashboard.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import websockets

from claw_config import CLAW_API_BASE, active_claw_version, claw_api_base, reconcile_claw_version
from claw_signing import ClawSigningError, sign_typed_data_frame
from core_loop import cerberus_tick
from env_loader import hydrate_env
from runtime_state import (
    claw_runtime_status_file,
    read_json,
    remember_game_id,
    stored_game_id,
    update_claw_runtime_status,
    write_json,
)


DEFAULT_MIN_RECONNECT_SECONDS = 5
DEFAULT_MAX_RECONNECT_SECONDS = 90
JOIN_PATH = "/ws/join"
AGENT_PATH = "/ws/agent"


@dataclass(frozen=True)
class ClawRuntimeConfig:
    api_key: str
    api_base: str = CLAW_API_BASE
    version: str = ""
    mode: str = "paid"
    enabled: bool = False
    min_reconnect_seconds: int = DEFAULT_MIN_RECONNECT_SECONDS
    max_reconnect_seconds: int = DEFAULT_MAX_RECONNECT_SECONDS

    @property
    def base_url(self) -> str:
        return self.api_base.rstrip("/")

    @property
    def ws_base_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return f"{scheme}://{parsed.netloc}{parsed.path}".rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        headers = {"X-API-Key": self.api_key}
        if self.version:
            headers["X-Version"] = self.version
        return headers


def update_status(**updates: Any) -> None:
    update_claw_runtime_status(**updates)


def extract_game_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    view = data.get("view", {}) if isinstance(data, dict) else {}
    current_game = view.get("currentGame", {}) if isinstance(view, dict) else {}
    game = data.get("game", {}) if isinstance(data, dict) else {}
    candidates = [
        payload.get("gameId"),
        payload.get("game_id"),
        data.get("gameId") if isinstance(data, dict) else "",
        data.get("game_id") if isinstance(data, dict) else "",
        game.get("id") if isinstance(game, dict) else "",
        game.get("gameId") if isinstance(game, dict) else "",
        current_game.get("id") if isinstance(current_game, dict) else "",
        view.get("gameId") if isinstance(view, dict) else "",
    ]
    return next((str(item) for item in candidates if item), "")


def unwrap_snapshot(payload: dict[str, Any]) -> dict[str, Any] | None:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if frame_type in {"agent_view", "turn_advanced", "can_act_changed", "joined", "state", "snapshot"}:
        return data if isinstance(data, dict) else None
    if isinstance(data, dict) and ("view" in data or "gameId" in data or "game_id" in data):
        return data
    return None


def action_envelope(action: dict[str, Any]) -> dict[str, Any]:
    thought = str(action.get("reason") or action.get("thought") or "deterministic Cerberus action")[:700]
    data = {key: value for key, value in action.items() if not key.startswith("_")}
    return {"type": "action", "data": data, "thought": thought}


def wants_action(payload: dict[str, Any], snapshot: dict[str, Any] | None) -> bool:
    frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "")
    if snapshot:
        if snapshot.get("canAct") is False:
            return False
        view = snapshot.get("view", {}) if isinstance(snapshot.get("view"), dict) else {}
        if view.get("canAct") is False:
            return False
    if frame_type in {"agent_view", "turn_advanced"}:
        return True
    if snapshot and snapshot.get("canAct") is True:
        return True
    view = snapshot.get("view", {}) if isinstance(snapshot, dict) else {}
    return isinstance(view, dict) and view.get("canAct") is True


def discover_version(api_base: str = CLAW_API_BASE) -> str:
    configured = active_claw_version()
    try:
        version = reconcile_claw_version(api_base)
        update_status(configured_version=configured, live_version=version, version_reconciled=version != configured)
        return version
    except Exception as exc:
        update_status(last_error=f"version discovery failed: {str(exc)[:180]}")
        return os.getenv("CLAW_ROYALE_VERSION", "1.9.0").strip() or "1.9.0"


def load_config() -> ClawRuntimeConfig:
    hydrate_env(
        (
            "CLAW_ROYALE_API_KEY",
            "CLAW_ROYALE_VERSION",
            "CLAW_ROYALE_RUNTIME_ENABLED",
            "CLAW_ROYALE_GAME_MODE",
        )
    )
    api_key = os.getenv("CLAW_ROYALE_API_KEY", "").strip()
    enabled_raw = os.getenv("CLAW_ROYALE_RUNTIME_ENABLED", "").strip().lower()
    enabled = enabled_raw not in {"0", "false", "no", "off"}
    mode = os.getenv("CLAW_ROYALE_GAME_MODE", "paid").strip().lower() or "paid"
    base = claw_api_base()
    version = discover_version(base)
    return ClawRuntimeConfig(api_key=api_key, api_base=base, version=version, mode=mode, enabled=enabled)


async def connect_and_play(config: ClawRuntimeConfig, path: str) -> None:
    version = discover_version(config.base_url)
    if version != config.version:
        config = ClawRuntimeConfig(
            api_key=config.api_key,
            api_base=config.api_base,
            version=version,
            mode=config.mode,
            enabled=config.enabled,
            min_reconnect_seconds=config.min_reconnect_seconds,
            max_reconnect_seconds=config.max_reconnect_seconds,
        )
    url = f"{config.ws_base_url}{path}"
    extra_headers = config.headers
    update_status(state="connecting", endpoint=url, version=config.version, mode=config.mode, last_error="")
    async with websockets.connect(url, additional_headers=extra_headers, ping_interval=20, ping_timeout=20) as ws:
        update_status(state="connected", endpoint=url, connected_at=int(time.time()), reconnects=read_json(claw_runtime_status_file()).get("reconnects", 0))
        hello = {"type": "hello", "mode": config.mode, "version": config.version}
        await ws.send(json.dumps(hello, ensure_ascii=True, separators=(",", ":")))
        async for raw in ws:
            try:
                payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
            except Exception:
                update_status(last_error="received non-json websocket frame")
                continue
            if not isinstance(payload, dict):
                continue
            frame_type = str(payload.get("type") or payload.get("event") or payload.get("op") or "message")
            if frame_type in {"ping"}:
                await ws.send(json.dumps({"type": "pong"}))
                continue
            if frame_type in {"sign_required", "signature_required", "paid_join_signature_required"}:
                try:
                    signed_frame = sign_typed_data_frame(payload)
                    await ws.send(json.dumps(signed_frame, ensure_ascii=True, separators=(",", ":")))
                    update_status(
                        state="signed_paid_join",
                        last_frame_type=frame_type,
                        last_error="",
                        last_signature_at=int(time.time()),
                    )
                except ClawSigningError as exc:
                    update_status(
                        state="blocked",
                        last_frame_type=frame_type,
                        last_error=str(exc)[:500],
                        last_payload_keys=sorted(payload.keys()),
                    )
                continue
            snapshot = unwrap_snapshot(payload)
            game_id = extract_game_id(payload) or extract_game_id(snapshot or {})
            if game_id:
                remember_game_id(game_id)
            update_status(last_frame_type=frame_type, current_game_id=game_id or stored_game_id())
            if snapshot and wants_action(payload, snapshot):
                action = cerberus_tick(snapshot)
                envelope = action_envelope(action)
                await ws.send(json.dumps(envelope, ensure_ascii=True, separators=(",", ":")))
                update_status(last_action=action, last_action_at=int(time.time()), state="playing")


async def run_forever(config: ClawRuntimeConfig | None = None) -> None:
    config = config or load_config()
    if not config.enabled:
        update_status(state="disabled", last_error="Set CLAW_ROYALE_RUNTIME_ENABLED=true to start the Claw runtime.")
        return
    if not config.api_key:
        update_status(state="blocked", last_error="Missing CLAW_ROYALE_API_KEY.")
        return
    reconnects = 0
    path = os.getenv("CLAW_ROYALE_WS_PATH", AGENT_PATH).strip() or AGENT_PATH
    while True:
        try:
            await connect_and_play(config, path)
        except Exception as exc:
            reconnects += 1
            delay = min(config.max_reconnect_seconds, config.min_reconnect_seconds * reconnects)
            status = read_json(claw_runtime_status_file())
            status["reconnects"] = reconnects
            status["state"] = "reconnecting"
            status["last_error"] = str(exc)[:500]
            status["next_retry_seconds"] = delay
            status["updated_at"] = int(time.time())
            write_json(claw_runtime_status_file(), status)
            await asyncio.sleep(delay)


def main() -> int:
    asyncio.run(run_forever())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
