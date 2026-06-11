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
from claw_runtime import run_forever as run_claw_runtime  # noqa: E402
from env_loader import hydrate_env  # noqa: E402
from longterm_memory import LongTermMemoryStore  # noqa: E402
from memory_system import DEFAULT_MEMORY_DIR  # noqa: E402
from runtime_state import (
    append_stream_chat,
    claw_runtime_status_file,
    hellion_voice_lab,
    read_json,
    remember_game_id,
    stream_chat_messages,
    stored_game_id as runtime_stored_game_id,
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
        "memory_dir": ready.get("memory_dir", ""),
        "memory_writable": ready.get("memory_writable", False),
        "memory_error": ready.get("memory_error", ""),
        "longterm_memory_error": ready.get("longterm_memory_error", ""),
        "longterm_memory": ready.get("longterm_memory", {}),
        "env": ready.get("env", {}),
    }


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
    body {{ margin: 0; background: #0f1115; color: #f4f7fb; }}
    header {{ padding: 16px 20px; border-bottom: 1px solid #2a3140; display: flex; gap: 16px; align-items: center; justify-content: space-between; }}
    h1 {{ font-size: 18px; margin: 0; font-weight: 700; }}
    main {{ display: grid; grid-template-columns: 340px 1fr; min-height: calc(100vh - 58px); }}
    aside {{ border-right: 1px solid #2a3140; padding: 16px; overflow: auto; }}
    iframe {{ width: 100%; height: 100%; border: 0; background: #080a0d; }}
    .metric {{ border: 1px solid #2a3140; border-radius: 6px; padding: 10px; margin-bottom: 10px; background: #151923; }}
    .label {{ color: #98a2b3; font-size: 12px; }}
    .value {{ font-size: 14px; overflow-wrap: anywhere; margin-top: 4px; }}
    button, a.button {{ background: #e8edf7; color: #0f1115; border: 0; border-radius: 6px; padding: 8px 10px; text-decoration: none; cursor: pointer; }}
    .frame {{ min-height: calc(100vh - 58px); }}
    @media (max-width: 900px) {{ main {{ grid-template-columns: 1fr; }} aside {{ border-right: 0; border-bottom: 1px solid #2a3140; }} .frame {{ min-height: 72vh; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Hellion Dashboard</h1>
    <button onclick="refreshFeed()">Refresh Feed</button>
  </header>
  <main>
    <aside>
      <div class="metric"><div class="label">Service</div><div id="service" class="value">loading</div></div>
      <div class="metric"><div class="label">Current Game</div><div id="game" class="value">unknown</div></div>
      <div class="metric"><div class="label">Runtime Blockers</div><div id="blockers" class="value">loading</div></div>
      <div class="metric"><div class="label">Memory DB</div><div id="memory" class="value">loading</div></div>
      <div class="metric"><div class="label">Writable</div><div id="writable" class="value">loading</div></div>
      <div class="metric"><div class="label">Configured Env</div><div id="env" class="value">loading</div></div>
      <a id="open-feed" class="button" href="{initial_feed_url or spectate_base_url}" target="_blank" rel="noreferrer">Open Spectate</a>
    </aside>
    <section class="frame">
      <iframe id="feed" {initial_frame_attrs} title="Claw Royale live game feed"></iframe>
    </section>
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
      document.getElementById("memory").textContent = (mem.items || 0) + " items, " + (mem.bytes || 0) + " bytes";
      document.getElementById("writable").textContent = data.memory_writable ? "yes" : "no";
      document.getElementById("env").textContent = Object.entries(data.env || {{}}).filter(([,v]) => v).map(([k]) => k).join(", ") || "none";
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
