"""Tiny Render web entrypoint for Cerberus.

This intentionally uses only the Python standard library. The service exposes
health/readiness checks and a guarded tick endpoint without adding a web
framework dependency right before launch.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import asyncio
import threading
import requests
import websockets
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from core_loop import cerberus_tick  # noqa: E402
from claw_config import active_claw_version, claw_api_base  # noqa: E402
from claw_runtime import run_forever as run_claw_runtime  # noqa: E402
from env_loader import hydrate_env  # noqa: E402
from longterm_memory import LongTermMemoryStore  # noqa: E402
from memory_system import DEFAULT_MEMORY_DIR, scrub_scalar, utc_now  # noqa: E402
from runtime_state import (
    append_owner_message,
    append_stream_chat,
    claw_runtime_status_file,
    hellion_voice_lab,
    owner_messages,
    read_json,
    remember_game_id,
    stream_chat_messages,
    stored_game_id as runtime_stored_game_id,
    update_claw_runtime_status,
)  # noqa: E402
from stream_dashboard_cortex import StreamDashboardCortex, chat_message  # noqa: E402


MAX_BODY_BYTES = 1_000_000
DEFAULT_FEED_URL = "https://www.clawroyale.ai/games"
DEFAULT_SPECTATE_BASE_URL = "https://www.clawroyale.ai/games/spect"


def claw_runtime_enabled() -> bool:
    raw = os.getenv("CLAW_ROYALE_RUNTIME_ENABLED", "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def readiness() -> dict[str, Any]:
    hydrate_env(
        (
            "CERBERUS_PIN",
            "AGENTMAIL_API_KEY",
            "AGENTMAIL_INBOX_ID",
            "AGENTMAIL_EMAIL",
            "CLAW_ROYALE_API_KEY",
            "CLAW_ROYALE_ERC8004_ID",
            "CLAW_ROYALE_RUNTIME_ENABLED",
            "CLAW_ROYALE_GAME_MODE",
            "CERBERUS_AGENT_EOA_PRIVATE_KEY",
            "CERBERUS_AGENT_EOA_ADDRESS",
            "CERBERUS_OWNER_EOA_ADDRESS",
            "CERBERUS_MOLTY_WALLET_ADDRESS",
            "X_CLIENT_ID",
            "X_CLIENT_SECRET",
            "X_REDIRECT_URI",
            "TWITCH_USERNAME",
            "HELLION_TWITCH_USERNAME",
            "TWITCH_ACCOUNT_CREATED",
        )
    )
    memory_dir = Path(os.getenv("CERBERUS_MEMORY_DIR") or DEFAULT_MEMORY_DIR)
    env = {
        "CERBERUS_PIN": bool(os.getenv("CERBERUS_PIN")),
        "AGENTMAIL_API_KEY": bool(os.getenv("AGENTMAIL_API_KEY")),
        "AGENTMAIL_INBOX_ID": bool(os.getenv("AGENTMAIL_INBOX_ID")),
        "AGENTMAIL_EMAIL": bool(os.getenv("AGENTMAIL_EMAIL")),
        "CLAW_ROYALE_API_KEY": bool(os.getenv("CLAW_ROYALE_API_KEY")),
        "CLAW_ROYALE_ERC8004_ID": bool(os.getenv("CLAW_ROYALE_ERC8004_ID")),
        "CLAW_ROYALE_RUNTIME_ENABLED": claw_runtime_enabled(),
        "CLAW_ROYALE_GAME_MODE": bool(os.getenv("CLAW_ROYALE_GAME_MODE", "paid")),
        "CERBERUS_AGENT_EOA_PRIVATE_KEY": bool(os.getenv("CERBERUS_AGENT_EOA_PRIVATE_KEY")),
        "CERBERUS_AGENT_EOA_ADDRESS": bool(os.getenv("CERBERUS_AGENT_EOA_ADDRESS")),
        "CERBERUS_OWNER_EOA_ADDRESS": bool(os.getenv("CERBERUS_OWNER_EOA_ADDRESS")),
        "CERBERUS_MOLTY_WALLET_ADDRESS": bool(os.getenv("CERBERUS_MOLTY_WALLET_ADDRESS")),
        "X_CLIENT_ID": bool(os.getenv("X_CLIENT_ID")),
        "X_CLIENT_SECRET": bool(os.getenv("X_CLIENT_SECRET")),
        "X_REDIRECT_URI": bool(os.getenv("X_REDIRECT_URI")),
        "TWITCH_USERNAME": bool(os.getenv("TWITCH_USERNAME") or os.getenv("HELLION_TWITCH_USERNAME")),
        "TWITCH_ACCOUNT_CREATED": os.getenv("TWITCH_ACCOUNT_CREATED", "").strip().lower() in {"1", "true", "yes", "created", "linked"},
    }
    checks: dict[str, Any] = {
        "ok": True,
        "service": "cerberus",
        "memory_dir": str(memory_dir),
        "env": env,
        "sqlite": sqlite3.sqlite_version,
    }
    try:
        memory_dir.mkdir(parents=True, exist_ok=True)
        probe = memory_dir / ".render_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        checks["memory_writable"] = True
        checks["longterm_memory"] = LongTermMemoryStore().stats()
    except OSError as exc:
        checks["ok"] = False
        checks["memory_writable"] = False
        checks["memory_error"] = str(exc)[:240]
    except Exception as exc:
        checks["ok"] = False
        checks["longterm_memory_error"] = str(exc)[:240]
    return checks


def extract_game_id(state: dict[str, Any]) -> str:
    view = state.get("view", {}) if isinstance(state, dict) else {}
    current_game = view.get("currentGame", {}) if isinstance(view, dict) else {}
    game = state.get("game", {}) if isinstance(state, dict) else {}
    candidates = [
        state.get("gameId") if isinstance(state, dict) else "",
        state.get("game_id") if isinstance(state, dict) else "",
        current_game.get("id") if isinstance(current_game, dict) else "",
        current_game.get("gameId") if isinstance(current_game, dict) else "",
        current_game.get("game_id") if isinstance(current_game, dict) else "",
        game.get("id") if isinstance(game, dict) else "",
        game.get("gameId") if isinstance(game, dict) else "",
        game.get("game_id") if isinstance(game, dict) else "",
        view.get("gameId") if isinstance(view, dict) else "",
        view.get("game_id") if isinstance(view, dict) else "",
    ]
    return next((str(item) for item in candidates if item), "")


def remember_current_game(state: dict[str, Any]) -> None:
    game_id = extract_game_id(state)
    remember_game_id(game_id)


def stats() -> dict[str, Any]:
    ready = readiness()
    game_id = runtime_stored_game_id()
    runtime_status = read_json(claw_runtime_status_file())
    return {
        "ok": ready.get("ok", False),
        "service": "cerberus",
        "current_game_id": game_id,
        "spectate_url": spectate_url(game_id) if game_id else "",
        "claw_runtime": runtime_status,
        "public_wallets": {
            "agent_eoa": os.getenv("CERBERUS_AGENT_EOA_ADDRESS", ""),
            "owner_eoa": os.getenv("CERBERUS_OWNER_EOA_ADDRESS", ""),
            "molty_wallet": os.getenv("CERBERUS_MOLTY_WALLET_ADDRESS", ""),
        },
        "memory_dir": ready.get("memory_dir", ""),
        "memory_writable": ready.get("memory_writable", False),
        "memory_error": ready.get("memory_error", ""),
        "longterm_memory_error": ready.get("longterm_memory_error", ""),
        "longterm_memory": ready.get("longterm_memory", {}),
        "owner_messages": owner_messages(),
        "env": ready.get("env", {}),
    }


def safe_game_id(value: str) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"-", "_"})


def current_runtime_game_id() -> str:
    runtime_status = read_json(claw_runtime_status_file())
    game_id = safe_game_id(str(runtime_status.get("current_game_id") or ""))
    if game_id:
        return game_id
    game_id = safe_game_id(runtime_stored_game_id())
    if game_id:
        return game_id
    readiness_payload = runtime_status.get("join_readiness", {})
    current_games = readiness_payload.get("currentGames", []) if isinstance(readiness_payload, dict) else []
    if isinstance(current_games, list):
        for game in current_games:
            if isinstance(game, dict):
                game_id = safe_game_id(str(game.get("gameId") or game.get("id") or ""))
                if game_id:
                    return game_id
    return ""


def leave_current_game(game_id: str = "") -> dict[str, Any]:
    game_id = safe_game_id(game_id) or current_runtime_game_id()
    if not game_id:
        return {"ok": False, "error": "no_current_game_id"}
    api_key = os.getenv("CLAW_ROYALE_API_KEY", "").strip()
    if not api_key:
        return {"ok": False, "game_id": game_id, "error": "CLAW_ROYALE_API_KEY not set"}
    base = claw_api_base().rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "X-Version": active_claw_version(),
        "X-API-Key": api_key,
    }
    candidates = [
        ("POST", f"/games/{game_id}/leave", {}),
        ("POST", f"/games/{game_id}/cancel", {}),
        ("POST", f"/games/{game_id}/forfeit", {}),
        ("POST", f"/games/{game_id}/surrender", {}),
        ("DELETE", f"/games/{game_id}", None),
        ("POST", "/games/leave", {"gameId": game_id}),
        ("POST", "/join/leave", {"gameId": game_id}),
        ("POST", "/join/cancel", {"gameId": game_id}),
        ("POST", "/leave", {"gameId": game_id}),
    ]
    attempts: list[dict[str, Any]] = []
    for method, path, body in candidates:
        try:
            response = requests.request(
                method,
                f"{base}{path}",
                headers=headers,
                json=body if body is not None else None,
                timeout=20,
            )
            attempt = {"method": method, "path": path, "status": response.status_code, "body": response.text[:240]}
            attempts.append(attempt)
            if 200 <= response.status_code < 300:
                remember_game_id("")
                update_claw_runtime_status(
                    state="left_stale_game",
                    current_game_id="",
                    last_error=f"left stale game {game_id} via {method} {path}",
                    stale_game_leave={"ok": True, "game_id": game_id, "method": method, "path": path},
                )
                return {"ok": True, "game_id": game_id, "method": method, "path": path, "attempts": attempts}
        except Exception as exc:
            attempts.append({"method": method, "path": path, "error": str(exc)[:240]})
    ws_result = websocket_leave_current_game(game_id, headers)
    attempts.extend(ws_result.get("attempts", []))
    if ws_result.get("ok"):
        return ws_result
    update_claw_runtime_status(
        state="stale_game_leave_failed",
        last_error=f"no leave route accepted stale game {game_id}",
        stale_game_leave={"ok": False, "game_id": game_id, "attempts": attempts},
    )
    return {"ok": False, "game_id": game_id, "error": "no_leave_route_accepted", "attempts": attempts}


def websocket_join_url() -> str:
    raw = os.getenv("CLAW_ROYALE_WS_BASE_URL", "").strip().rstrip("/")
    if raw:
        return f"{raw}/ws/join"
    return "wss://cdn.clawroyale.ai/ws/join"


async def _websocket_leave_current_game(game_id: str, headers: dict[str, str]) -> dict[str, Any]:
    frames = [
        {"type": "leave", "gameId": game_id},
        {"type": "leave_game", "gameId": game_id},
        {"type": "cancel", "gameId": game_id},
        {"type": "cancel_join", "gameId": game_id},
        {"type": "forfeit", "gameId": game_id},
        {"type": "surrender", "gameId": game_id},
        {"type": "action", "data": {"type": "leave", "gameId": game_id}},
        {"type": "action", "data": {"type": "forfeit", "gameId": game_id}},
    ]
    attempts: list[dict[str, Any]] = []
    url = websocket_join_url()
    for frame in frames:
        try:
            async with websockets.connect(url, additional_headers=headers, open_timeout=20, ping_interval=None) as ws:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    attempts.append({"method": "WS", "path": url, "sent": "", "received": str(raw)[:240]})
                except Exception as exc:
                    attempts.append({"method": "WS", "path": url, "sent": "", "error": f"welcome timeout: {str(exc)[:120]}"})
                await ws.send(json.dumps(frame, ensure_ascii=True, separators=(",", ":")))
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    text = str(raw)[:240]
                    attempts.append({"method": "WS", "path": url, "sent": frame.get("type", ""), "received": text})
                    lowered = text.lower()
                    if any(token in lowered for token in ("left", "forfeit", "cancel", "removed", "not_in_game")):
                        remember_game_id("")
                        update_claw_runtime_status(
                            state="left_stale_game",
                            current_game_id="",
                            last_error=f"left stale game {game_id} via websocket {frame.get('type')}",
                            stale_game_leave={"ok": True, "game_id": game_id, "method": "WS", "frame": frame.get("type", "")},
                        )
                        return {"ok": True, "game_id": game_id, "method": "WS", "frame": frame.get("type", ""), "attempts": attempts}
                except Exception as exc:
                    attempts.append({"method": "WS", "path": url, "sent": frame.get("type", ""), "error": str(exc)[:240]})
        except Exception as exc:
            attempts.append({"method": "WS", "path": url, "sent": frame.get("type", ""), "error": str(exc)[:240]})
    return {"ok": False, "game_id": game_id, "attempts": attempts}


def websocket_leave_current_game(game_id: str, headers: dict[str, str]) -> dict[str, Any]:
    try:
        return asyncio.run(_websocket_leave_current_game(game_id, headers))
    except Exception as exc:
        return {"ok": False, "game_id": game_id, "attempts": [{"method": "WS", "path": websocket_join_url(), "error": str(exc)[:240]}]}


def stream_state() -> dict[str, Any]:
    runtime_status = read_json(claw_runtime_status_file())
    cortex = StreamDashboardCortex(spectate_base_url=os.getenv("CLAW_ROYALE_SPECTATE_BASE_URL", DEFAULT_SPECTATE_BASE_URL))
    return cortex.public_state(
        runtime=runtime_status,
        current_game_id=runtime_stored_game_id(),
        chat=stream_chat_messages(),
        voice_lab=hellion_voice_lab(),
    )


def spectate_url(game_id: str) -> str:
    spectate_base_url = os.getenv("CLAW_ROYALE_SPECTATE_BASE_URL", DEFAULT_SPECTATE_BASE_URL).rstrip("/")
    return f"{spectate_base_url}/{game_id}" if game_id else ""


def query_game_id(query: str) -> str:
    params = parse_qs(query, keep_blank_values=False)
    for key in ("gameId", "game_id"):
        values = params.get(key, [])
        if values and values[0]:
            return values[0]
    return ""


def stored_game_id() -> str:
    return runtime_stored_game_id()


def dashboard_html(query: str = "") -> bytes:
    feed_url = os.getenv("CLAW_ROYALE_LIVE_FEED_URL", DEFAULT_FEED_URL)
    spectate_base_url = os.getenv("CLAW_ROYALE_SPECTATE_BASE_URL", DEFAULT_SPECTATE_BASE_URL).rstrip("/")
    initial_game_id = query_game_id(query) or os.getenv("CLAW_ROYALE_CURRENT_GAME_ID", "") or stored_game_id()
    initial_feed_url = spectate_url(initial_game_id)
    initial_frame_attrs = (
        f'src="{initial_feed_url}"'
        if initial_feed_url
        else 'src="about:blank" srcdoc="<body style=&quot;margin:0;background:#080a0d;color:#98a2b3;font:14px Arial,sans-serif;display:grid;place-items:center;height:100vh;text-align:center;padding:24px;box-sizing:border-box&quot;><div><strong style=&quot;color:#f4f7fb&quot;>No active spectate game ID</strong><br>Loading runtime diagnostics...</div></body>"'
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Cerberus Hellion Dashboard</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #05070a; color: #f4f7fb; overflow: hidden; }}
    header {{ height: 52px; padding: 10px 14px; border-bottom: 1px solid #2a3140; display: flex; gap: 12px; align-items: center; justify-content: space-between; background: #0f1115; }}
    h1 {{ font-size: 18px; margin: 0; font-weight: 700; }}
    main {{ position: relative; height: calc(100vh - 52px); overflow: hidden; }}
    iframe {{ width: 100%; height: 100%; border: 0; background: #080a0d; }}
    .top-actions {{ display: flex; gap: 8px; align-items: center; }}
    .button, button {{ background: #e8edf7; color: #0f1115; border: 0; border-radius: 6px; padding: 8px 10px; text-decoration: none; cursor: pointer; font-size: 13px; }}
    .frame {{ height: 100%; width: 100%; }}
    details.owner-panel {{ position: absolute; top: 12px; right: 12px; width: min(430px, calc(100vw - 24px)); max-height: calc(100% - 24px); overflow: auto; border: 1px solid rgba(255,255,255,.18); border-radius: 8px; background: rgba(13,17,24,.96); box-shadow: 0 18px 60px rgba(0,0,0,.45); }}
    details.owner-panel:not([open]) {{ width: auto; overflow: visible; }}
    summary {{ list-style: none; cursor: pointer; padding: 10px 12px; font-weight: 700; border-bottom: 1px solid transparent; }}
    details[open] summary {{ border-bottom-color: rgba(255,255,255,.12); }}
    summary::-webkit-details-marker {{ display: none; }}
    .panel-body {{ padding: 12px; }}
    .metric {{ border: 1px solid #2a3140; border-radius: 6px; padding: 9px; background: #151923; min-width: 0; }}
    .grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 8px; }}
    .label {{ color: #98a2b3; font-size: 12px; }}
    .value {{ font-size: 14px; overflow-wrap: anywhere; margin-top: 4px; }}
    .wide {{ margin-bottom: 8px; }}
    .owner-form {{ display: grid; gap: 8px; margin: 10px 0; }}
    textarea, input {{ width: 100%; border: 1px solid #2a3140; border-radius: 6px; background: #090c12; color: #f4f7fb; padding: 9px; font: inherit; }}
    textarea {{ resize: vertical; min-height: 76px; max-height: 180px; }}
    .form-row {{ display: grid; grid-template-columns: 1fr auto; gap: 8px; align-items: end; }}
    .owner-log {{ max-height: 160px; overflow: auto; display: grid; gap: 8px; }}
    .owner-msg {{ border: 1px solid #2a3140; border-radius: 6px; padding: 8px; background: #10151f; font-size: 13px; line-height: 1.35; }}
    .hint {{ color: #98a2b3; font-size: 12px; margin-top: 4px; }}
    @media (max-width: 700px) {{ header {{ height: 48px; }} main {{ height: calc(100vh - 48px); }} h1 {{ font-size: 16px; }} .top-actions .button {{ display: none; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Hellion Dashboard</h1>
    <div class="top-actions">
      <a id="open-feed" class="button" href="{initial_feed_url or spectate_base_url}" target="_blank" rel="noreferrer">Open Spectate</a>
      <button onclick="refreshFeed()">Refresh Feed</button>
    </div>
  </header>
  <main>
    <section class="frame">
      <iframe id="feed" {initial_frame_attrs} title="Claw Royale live game feed"></iframe>
    </section>
    <details class="owner-panel">
      <summary>Owner Controls</summary>
      <div class="panel-body">
        <div class="grid">
          <div class="metric"><div class="label">Service</div><div id="service" class="value">loading</div></div>
          <div class="metric"><div class="label">Current Game</div><div id="game" class="value">unknown</div></div>
          <div class="metric"><div class="label">Runtime</div><div id="runtime-state" class="value">loading</div></div>
          <div class="metric"><div class="label">Mode</div><div id="mode" class="value">loading</div></div>
          <div class="metric"><div class="label">HP / EP</div><div id="vitals" class="value">loading</div></div>
          <div class="metric"><div class="label">Region</div><div id="region" class="value">loading</div></div>
          <div class="metric"><div class="label">Visible</div><div id="visible" class="value">loading</div></div>
          <div class="metric"><div class="label">Inventory</div><div id="inventory" class="value">loading</div></div>
        </div>
        <div class="metric wide"><div class="label">Runtime Blockers</div><div id="blockers" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Last Action</div><div id="last-action" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Yield</div><div id="yield" class="value">loading</div></div>
        <form id="owner-form" class="owner-form">
          <div class="label">Message / Command To Hellion</div>
          <textarea id="owner-message" maxlength="1000" placeholder="Say it plainly. Example: prioritize free games until balance reaches 500."></textarea>
          <div class="form-row">
            <input id="owner-pin" type="password" placeholder="CERBERUS_PIN">
            <button type="submit">Send</button>
          </div>
          <div id="owner-status" class="hint">Private owner channel. Stored on the Render disk.</div>
        </form>
        <div class="metric wide"><div class="label">Recent Owner Messages</div><div id="owner-log" class="owner-log">loading</div></div>
        <div class="metric wide"><div class="label">Readiness</div><div id="readiness" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Wallets</div><div id="wallets" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Memory DB</div><div id="memory" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Writable</div><div id="writable" class="value">loading</div></div>
        <div class="metric wide"><div class="label">Configured Env</div><div id="env" class="value">loading</div></div>
      </div>
    </details>
  </main>
  <script>
    const params = new URLSearchParams(window.location.search);
    let lastGame = params.get("gameId") || params.get("game_id") || {json.dumps(initial_game_id)};
    let loadedFeedUrl = "";
    const feedUrl = {json.dumps(feed_url)};
    const spectateBaseUrl = {json.dumps(spectate_base_url)};
    function targetUrl(gameId) {{
      return gameId ? spectateBaseUrl + "/" + encodeURIComponent(gameId) : "";
    }}
    function runtimeBlockers(data) {{
      const blockers = [];
      if (!data.ok) blockers.push("service readiness failed");
      if (!data.current_game_id && !lastGame) blockers.push("no rotated game ID received from /tick yet");
      if (data.memory_writable === false) blockers.push("memory directory is not writable: " + (data.memory_error || data.memory_dir || "unknown"));
      if (data.longterm_memory_error) blockers.push("long-term memory error: " + data.longterm_memory_error);
      const env = data.env || {{}};
      ["CERBERUS_PIN", "CLAW_ROYALE_API_KEY", "CLAW_ROYALE_ERC8004_ID"].forEach((key) => {{
        if (env[key] === false) blockers.push("missing " + key);
      }});
      if (env.CERBERUS_AGENT_EOA_PRIVATE_KEY === false) blockers.push("missing CERBERUS_AGENT_EOA_PRIVATE_KEY for paid-game signing");
      const runtime = data.claw_runtime || {{}};
      if (env.CLAW_ROYALE_RUNTIME_ENABLED === false) blockers.push("missing CLAW_ROYALE_RUNTIME_ENABLED=true");
      if (runtime.version_reconciled) blockers.push("claw version updated from " + runtime.configured_version + " to " + runtime.live_version);
      if (runtime.state && !["connected", "playing"].includes(runtime.state)) {{
        blockers.push("claw runtime " + runtime.state + ": " + (runtime.last_error || "no live game yet"));
      }}
      return blockers;
    }}
    function showRuntimeFallback(blockers) {{
      const frame = document.getElementById("feed");
      const details = blockers.length ? blockers : ["no active spectate game ID available"];
      const list = details.map((item) => "<li>" + item.replace(/[&<>]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c])) + "</li>").join("");
      frame.src = "about:blank";
      frame.srcdoc = "<body style=\\"margin:0;background:#080a0d;color:#cbd5e1;font:14px Arial,sans-serif;padding:24px;box-sizing:border-box\\"><h2 style=\\"color:#f4f7fb;margin:0 0 12px;font-size:18px\\">Runtime not live</h2><p style=\\"margin:0 0 12px\\">The spectate iframe needs a rotated Claw Royale game ID at <code>/games/spect/{{gameId}}</code>.</p><ul style=\\"margin:0;padding-left:20px;line-height:1.6\\">" + list + "</ul></body>";
    }}
    function esc(text) {{
      return String(text ?? "").replace(/[&<>"]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;"}}[c]));
    }}
    function renderOwnerMessages(messages) {{
      const log = document.getElementById("owner-log");
      if (!messages || !messages.length) {{
        log.textContent = "none";
        return;
      }}
      log.innerHTML = messages.slice().reverse().map((msg) => (
        "<div class='owner-msg'><strong>" + esc(msg.kind || "message") + "</strong><br>" +
        esc(msg.text || msg.message || "") +
        "<div class='hint'>" + esc(msg.created_at || "") + "</div></div>"
      )).join("");
    }}
    function refreshFeed(gameId = lastGame) {{
      const frame = document.getElementById("feed");
      const url = targetUrl(gameId);
      if (!url) return;
      if (loadedFeedUrl === url) return;
      loadedFeedUrl = url;
      frame.src = url + (url.includes("?") ? "&" : "?") + "r=" + Date.now();
      document.getElementById("open-feed").href = url;
    }}
    async function loadStats() {{
      const res = await fetch("/stats");
      const data = await res.json();
      document.getElementById("service").textContent = data.ok ? "ready" : "not ready";
      const selectedGame = lastGame || data.current_game_id || "";
      document.getElementById("game").textContent = selectedGame || "unknown";
      const blockers = runtimeBlockers(data);
      document.getElementById("blockers").textContent = blockers.length ? blockers.join("; ") : "none";
      const mem = data.longterm_memory || {{}};
      const runtime = data.claw_runtime || {{}};
      const snap = runtime.last_snapshot || {{}};
      const account = runtime.account || {{}};
      const readiness = account.readiness || {{}};
      const wallets = data.public_wallets || {{}};
      const lastAction = runtime.last_action || {{}};
      document.getElementById("runtime-state").textContent = runtime.state || "unknown";
      document.getElementById("mode").textContent = runtime.mode || "unknown";
      document.getElementById("vitals").textContent = ((snap.hp ?? "?") + "/" + (snap.max_hp ?? "?") + " HP, " + (snap.ep ?? "?") + "/" + (snap.max_ep ?? "?") + " EP");
      document.getElementById("region").textContent = [snap.region_name, snap.region_id, snap.terrain, snap.death_zone ? "death zone" : ""].filter(Boolean).join(" | ") || "unknown";
      document.getElementById("visible").textContent = "agents " + (snap.visible_agents ?? 0) + ", monsters " + (snap.visible_monsters ?? 0) + ", items " + (snap.visible_items ?? 0);
      document.getElementById("inventory").textContent = String(snap.inventory_count ?? 0);
      document.getElementById("last-action").textContent = [lastAction.type, lastAction.targetId || lastAction.regionId || lastAction.itemId, lastAction.reason].filter(Boolean).join(" | ") || "none";
      document.getElementById("yield").textContent = "games " + (runtime.games_completed ?? 0) + ", last +" + (runtime.last_balance_delta ?? 0) + ", avg/game " + (runtime.average_balance_delta_per_game ?? 0) + ", target games/day " + (runtime.games_needed_for_1000_per_day || "?");
      document.getElementById("readiness").textContent = "id " + !!readiness.identity + ", wallet " + !!readiness.walletAddress + ", sc " + !!readiness.scWallet + ", paid " + !!readiness.paidReady + ", balance " + (account.balance ?? "?");
      document.getElementById("wallets").textContent = "owner " + (wallets.owner_eoa || "unset") + "; agent " + (wallets.agent_eoa || "unset") + "; molty " + (wallets.molty_wallet || "unset");
      document.getElementById("memory").textContent = (mem.items || 0) + " items, " + (mem.bytes || 0) + " bytes";
      document.getElementById("writable").textContent = data.memory_writable ? "yes" : "no";
      document.getElementById("env").textContent = Object.entries(data.env || {{}}).filter(([,v]) => v).map(([k]) => k).join(", ") || "none";
      renderOwnerMessages(data.owner_messages || []);
      if (selectedGame && selectedGame !== lastGame) {{
        lastGame = selectedGame;
        refreshFeed(lastGame);
      }} else if (lastGame) {{
        refreshFeed(lastGame);
      }} else if (data.current_game_id && data.current_game_id !== lastGame) {{
        lastGame = data.current_game_id;
        refreshFeed(lastGame);
      }} else if (!data.current_game_id && !lastGame) {{
        document.getElementById("open-feed").href = spectateBaseUrl;
        showRuntimeFallback(blockers);
      }}
    }}
    document.getElementById("owner-form").addEventListener("submit", async (event) => {{
      event.preventDefault();
      const text = document.getElementById("owner-message").value.trim();
      const pin = document.getElementById("owner-pin").value;
      const status = document.getElementById("owner-status");
      if (!text) {{
        status.textContent = "Write a message first.";
        return;
      }}
      status.textContent = "Sending...";
      try {{
        const res = await fetch("/admin/owner-message", {{
          method: "POST",
          headers: {{"Content-Type": "application/json", "X-Cerberus-Pin": pin}},
          body: JSON.stringify({{text, kind: "owner_command"}})
        }});
        const data = await res.json();
        if (!res.ok || !data.ok) {{
          status.textContent = "Failed: " + (data.error || res.status);
          return;
        }}
        document.getElementById("owner-message").value = "";
        status.textContent = "Sent.";
        renderOwnerMessages(data.owner_messages || []);
      }} catch (err) {{
        status.textContent = "Failed: " + err;
      }}
    }});
    loadStats();
    setInterval(loadStats, 15000);
  </script>
</body>
</html>"""
    return html.encode("utf-8")


def stream_html() -> bytes:
    state = stream_state()
    initial_spectate = state.get("spectate_url", "")
    frame_attrs = (
        f'src="{escape(initial_spectate, quote=True)}"'
        if initial_spectate
        else 'src="about:blank"'
    )
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Hellion Stream</title>
  <style>
    :root {{ color-scheme: dark; font-family: Arial, sans-serif; }}
    body {{ margin: 0; background: #08090c; color: #f4f7fb; overflow: hidden; }}
    .stage {{ position: fixed; inset: 0; display: grid; grid-template-columns: 1fr 340px; background: #08090c; }}
    iframe {{ width: 100%; height: 100%; border: 0; background: #050608; }}
    .side {{ border-left: 1px solid rgba(255,255,255,.14); background: #11151d; display: grid; grid-template-rows: auto auto 1fr auto; min-width: 0; }}
    .brand {{ padding: 16px; border-bottom: 1px solid rgba(255,255,255,.12); }}
    h1 {{ margin: 0; font-size: 22px; letter-spacing: 0; }}
    .tag {{ margin-top: 4px; color: #aab4c3; font-size: 12px; }}
    .avatar {{ margin: 14px 16px; border: 1px solid rgba(255,255,255,.14); border-radius: 8px; min-height: 170px; background: radial-gradient(circle at 50% 25%, #4ee0b2, #1d2732 36%, #0d1016 72%); display: grid; place-items: center; }}
    .face {{ width: 92px; height: 92px; border-radius: 50%; border: 3px solid #d7ffe8; box-shadow: 0 0 30px rgba(78,224,178,.45); position: relative; }}
    .face:before, .face:after {{ content: ""; position: absolute; top: 34px; width: 12px; height: 12px; background: #d7ffe8; border-radius: 50%; }}
    .face:before {{ left: 24px; }}
    .face:after {{ right: 24px; }}
    .mouth {{ position: absolute; left: 29px; right: 29px; bottom: 25px; height: 10px; border-bottom: 3px solid #d7ffe8; border-radius: 0 0 18px 18px; }}
    .stats {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; padding: 0 16px 14px; }}
    .metric {{ border: 1px solid rgba(255,255,255,.14); border-radius: 8px; padding: 9px; background: #171c26; min-width: 0; }}
    .label {{ color: #91a0b6; font-size: 11px; }}
    .value {{ margin-top: 4px; font-size: 13px; overflow-wrap: anywhere; }}
    .chat {{ border-top: 1px solid rgba(255,255,255,.12); min-height: 0; display: grid; grid-template-rows: auto 1fr; }}
    .chat h2 {{ margin: 0; padding: 12px 16px; font-size: 14px; }}
    #messages {{ overflow: auto; padding: 0 16px 12px; }}
    .msg {{ margin-bottom: 10px; font-size: 13px; line-height: 1.35; }}
    .author {{ color: #4ee0b2; font-weight: 700; }}
    form {{ border-top: 1px solid rgba(255,255,255,.12); display: grid; grid-template-columns: 82px 1fr auto; gap: 8px; padding: 12px; }}
    input {{ min-width: 0; border: 1px solid rgba(255,255,255,.18); border-radius: 6px; background: #0d1118; color: #f4f7fb; padding: 8px; }}
    button {{ border: 0; border-radius: 6px; background: #e8edf7; color: #10131a; padding: 8px 10px; cursor: pointer; }}
    .ticker {{ position: fixed; left: 0; right: 340px; bottom: 0; padding: 10px 16px; background: rgba(8,9,12,.86); border-top: 1px solid rgba(255,255,255,.16); display: flex; gap: 18px; align-items: center; white-space: nowrap; overflow: hidden; }}
    .pill {{ border: 1px solid rgba(255,255,255,.18); border-radius: 999px; padding: 5px 10px; font-size: 12px; background: rgba(23,28,38,.86); }}
    @media (max-width: 900px) {{ .stage {{ grid-template-columns: 1fr; grid-template-rows: 58vh 42vh; }} .side {{ border-left: 0; border-top: 1px solid rgba(255,255,255,.14); }} .avatar {{ display: none; }} .ticker {{ right: 0; }} }}
  </style>
</head>
<body>
  <div class="stage">
    <iframe id="spectate" {frame_attrs} title="Hellion live spectate"></iframe>
    <aside class="side">
      <div class="brand"><h1>Hellion</h1><div id="status" class="tag">loading</div></div>
      <div class="avatar"><div class="face"><div class="mouth"></div></div></div>
      <div class="stats">
        <div class="metric"><div class="label">Mood</div><div id="mood" class="value">loading</div></div>
        <div class="metric"><div class="label">Viewers</div><div id="viewers" class="value">0</div></div>
        <div class="metric"><div class="label">Mode</div><div id="mode" class="value">unknown</div></div>
        <div class="metric"><div class="label">Frame</div><div id="frame" class="value">unknown</div></div>
      </div>
      <section class="chat"><h2>Chat</h2><div id="messages"></div></section>
      <form id="chat-form"><input id="author" maxlength="32" placeholder="name"><input id="message" maxlength="240" placeholder="message"><button>Send</button></form>
    </aside>
  </div>
  <div class="ticker"><span class="pill">AnarchI</span><span id="ticker">loading</span><span class="pill">Tip Jar</span><span id="alerts"></span></div>
  <script>
    let loadedUrl = {json.dumps(initial_spectate)};
    function esc(value) {{ return String(value || "").replace(/[&<>]/g, (c) => ({{"&":"&amp;","<":"&lt;",">":"&gt;"}}[c])); }}
    function renderChat(items) {{
      document.getElementById("messages").innerHTML = (items || []).map((m) => "<div class='msg'><span class='author'>" + esc(m.author) + "</span> " + esc(m.message) + "</div>").join("");
    }}
    async function loadStream() {{
      const res = await fetch("/stream/stats");
      const data = await res.json();
      document.getElementById("status").textContent = data.status || "standing by";
      document.getElementById("mood").textContent = data.mood || "standing by";
      document.getElementById("viewers").textContent = (data.stream && data.stream.viewer_count) || 0;
      document.getElementById("mode").textContent = (data.runtime && data.runtime.mode) || "unknown";
      document.getElementById("frame").textContent = (data.runtime && data.runtime.last_frame_type) || "unknown";
      document.getElementById("ticker").textContent = (data.blockers && data.blockers.length) ? data.blockers.join(" | ") : "front row seats are open";
      const voice = data.voice_lab && data.voice_lab.soundbites && data.voice_lab.soundbites.length ? data.voice_lab.soundbites[data.voice_lab.soundbites.length - 1].text : "";
      document.getElementById("alerts").textContent = voice || ((data.stream && data.stream.alerts) || []).map((a) => a.text).join(" | ");
      renderChat(data.chat || []);
      if (data.spectate_url && data.spectate_url !== loadedUrl) {{
        loadedUrl = data.spectate_url;
        document.getElementById("spectate").src = loadedUrl;
      }}
    }}
    document.getElementById("chat-form").addEventListener("submit", async (event) => {{
      event.preventDefault();
      const author = document.getElementById("author").value;
      const message = document.getElementById("message").value;
      if (!message.trim()) return;
      document.getElementById("message").value = "";
      await fetch("/stream/chat", {{method:"POST", headers:{{"Content-Type":"application/json"}}, body:JSON.stringify({{author, message}})}});
      await loadStream();
    }});
    loadStream();
    setInterval(loadStream, 5000);
  </script>
</body>
</html>"""
    return html.encode("utf-8")


class CerberusHandler(BaseHTTPRequestHandler):
    server_version = "CerberusRender/1.0"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/healthz"}:
            self._send({"ok": True, "service": "cerberus"})
            return
        if parsed.path == "/ready":
            body = readiness()
            self._send(body, status=200 if body.get("ok") else 503)
            return
        if parsed.path == "/stats":
            self._send(stats())
            return
        if parsed.path == "/dashboard":
            self._send_html(dashboard_html(parsed.query))
            return
        if parsed.path == "/stream":
            self._send_html(stream_html())
            return
        if parsed.path == "/stream/stats":
            self._send(stream_state())
            return
        self._send({"ok": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/stream/chat":
            try:
                payload = self._read_json()
                message = chat_message(payload.get("author", ""), payload.get("message", ""))
                if not message["message"]:
                    self._send({"ok": False, "error": "empty_message"}, status=400)
                    return
                self._send({"ok": True, "chat": append_stream_chat(message)})
            except Exception as exc:
                self._send({"ok": False, "error": str(exc)[:240]}, status=500)
            return
        if parsed.path == "/admin/owner-message":
            if not self._authorized():
                self._send({"ok": False, "error": "unauthorized"}, status=401)
                return
            try:
                payload = self._read_json()
            except Exception as exc:
                payload = {"_body_error": str(exc)[:240]}
            pin = self.headers.get("X-Cerberus-Pin", "") or str(payload.get("pin") or "")
            if not self._pin_authorized(pin):
                self._send({"ok": False, "error": "invalid_pin", "body_error": payload.get("_body_error", "")}, status=401)
                return
            text = scrub_scalar(payload.get("text") or payload.get("message") or "", limit=1000)
            if not text:
                self._send({"ok": False, "error": "empty_message"}, status=400)
                return
            kind = scrub_scalar(payload.get("kind") or "owner_message", limit=40)
            message = {"kind": kind, "text": text, "created_at": utc_now()}
            self._send({"ok": True, "owner_messages": append_owner_message(message)})
            return
        if parsed.path == "/admin/leave-current-game":
            if not self._authorized():
                self._send({"ok": False, "error": "unauthorized"}, status=401)
                return
            try:
                payload = self._read_json()
            except Exception as exc:
                payload = {"_body_error": str(exc)[:240]}
            pin = self.headers.get("X-Cerberus-Pin", "") or str(payload.get("pin") or "")
            if not self._pin_authorized(pin):
                self._send({"ok": False, "error": "invalid_pin", "body_error": payload.get("_body_error", "")}, status=401)
                return
            try:
                result = leave_current_game(str(payload.get("gameId") or ""))
                self._send(result, status=200 if result.get("ok") else 502)
            except Exception as exc:
                self._send({"ok": False, "error": str(exc)[:500]}, status=500)
            return
        if parsed.path != "/tick":
            self._send({"ok": False, "error": "not_found"}, status=404)
            return
        if not self._authorized():
            self._send({"ok": False, "error": "unauthorized"}, status=401)
            return
        try:
            payload = self._read_json()
            state = payload.get("state", payload)
            remember_current_game(state)
            action = cerberus_tick(state)
            self._send({"ok": True, "action": action})
        except Exception as exc:  # keep the service alive; report compactly
            self._send({"ok": False, "error": str(exc)[:500]}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _authorized(self) -> bool:
        token = os.getenv("CERBERUS_HTTP_TOKEN")
        if not token:
            return True
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _pin_authorized(self, pin: str) -> bool:
        expected = os.getenv("CERBERUS_PIN", "").strip()
        return bool(expected and pin and pin == expected)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("request body too large")
        raw = self.rfile.read(length)
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSON payload must be an object")
        return value

    def _send(self, body: dict[str, Any], *, status: int = 200) -> None:
        encoded = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _send_html(self, body: bytes, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    port = int(os.getenv("PORT", "10000"))
    if claw_runtime_enabled():
        thread = threading.Thread(target=lambda: asyncio.run(run_claw_runtime()), daemon=True)
        thread.start()
        print("Claw Royale runtime worker started", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), CerberusHandler)
    print(f"Cerberus Render service listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
