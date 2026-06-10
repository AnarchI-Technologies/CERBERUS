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
import threading
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
from claw_runtime import read_json as read_runtime_json  # noqa: E402
from claw_runtime import run_forever as run_claw_runtime  # noqa: E402
from claw_runtime import runtime_status_file  # noqa: E402
from env_loader import hydrate_env  # noqa: E402
from longterm_memory import LongTermMemoryStore  # noqa: E402
from memory_system import DEFAULT_MEMORY_DIR  # noqa: E402


MAX_BODY_BYTES = 1_000_000
DEFAULT_FEED_URL = "https://www.clawroyale.ai/games"
DEFAULT_SPECTATE_BASE_URL = "https://www.clawroyale.ai/games/spect"


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
            "X_CLIENT_ID",
            "X_CLIENT_SECRET",
            "X_REDIRECT_URI",
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
        "CLAW_ROYALE_RUNTIME_ENABLED": bool(os.getenv("CLAW_ROYALE_RUNTIME_ENABLED")),
        "CLAW_ROYALE_GAME_MODE": bool(os.getenv("CLAW_ROYALE_GAME_MODE")),
        "X_CLIENT_ID": bool(os.getenv("X_CLIENT_ID")),
        "X_CLIENT_SECRET": bool(os.getenv("X_CLIENT_SECRET")),
        "X_REDIRECT_URI": bool(os.getenv("X_REDIRECT_URI")),
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


def current_game_file() -> Path:
    memory_dir = Path(os.getenv("CERBERUS_MEMORY_DIR") or DEFAULT_MEMORY_DIR)
    return memory_dir / "current_game.json"


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
    if not game_id:
        return
    path = current_game_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"game_id": game_id}, ensure_ascii=True), encoding="utf-8")


def stats() -> dict[str, Any]:
    ready = readiness()
    game_id = ""
    try:
        path = current_game_file()
        if path.exists():
            game_id = json.loads(path.read_text(encoding="utf-8")).get("game_id", "")
    except Exception:
        game_id = ""
    runtime_status = read_runtime_json(runtime_status_file())
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
    try:
        path = current_game_file()
        if path.exists():
            return str(json.loads(path.read_text(encoding="utf-8")).get("game_id", ""))
    except Exception:
        return ""
    return ""


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
      const runtime = data.claw_runtime || {{}};
      if (env.CLAW_ROYALE_RUNTIME_ENABLED === false) blockers.push("missing CLAW_ROYALE_RUNTIME_ENABLED=true");
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
        self._send({"ok": False, "error": "not_found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
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
    if os.getenv("CLAW_ROYALE_RUNTIME_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        thread = threading.Thread(target=lambda: __import__("asyncio").run(run_claw_runtime()), daemon=True)
        thread.start()
        print("Claw Royale runtime worker started", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), CerberusHandler)
    print(f"Cerberus Render service listening on 0.0.0.0:{port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
