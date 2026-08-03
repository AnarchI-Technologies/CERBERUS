"""Render launcher for a loopback CERBERUS core and optional plugin worker."""

from __future__ import annotations

import json
import os
import re
import select
import signal
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from auth_gateway import handle_connection


TRUE_VALUES = {"1", "true", "yes", "on"}
CHILDREN: list[subprocess.Popen[bytes]] = []
STOPPING = threading.Event()


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        handle_connection(
            self.request,
            "127.0.0.1",
            self.server.core_port,
            self.server.token,
        )


class ThreadedProxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], core_port: int, token: str):
        self.core_port = core_port
        self.token = token
        super().__init__(address, ProxyHandler)


def parse_port(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        port = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be numeric") from exc
    if not 1024 <= port <= 65535:
        raise SystemExit(f"{name} must be between 1024 and 65535")
    return port


def agent_ids() -> list[str]:
    raw = os.getenv("CERBERUS_AGENT_IDS", "default")
    values: list[str] = []
    for item in raw.split(","):
        agent = item.strip().lower()
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,21}[a-z0-9])?", agent):
            raise SystemExit("CERBERUS_AGENT_IDS contains an invalid agent slug")
        if agent not in values:
            values.append(agent)
    if not values:
        raise SystemExit("CERBERUS_AGENT_IDS must name at least one agent")
    return values


def agent_environment(base: dict[str, str], agent: str, memory_dir: Path, tick_url: str) -> dict[str, str]:
    environment = base.copy()
    environment.update(
        {
            "CERBERUS_RUNTIME_AGENT_ID": agent,
            "CERBERUS_MEMORY_DIR": str(memory_dir / "agents" / agent),
            "CERBERUS_TICK_URL": tick_url,
            "CLAW_ROYALE_RUNTIME_ENABLED": "true",
        }
    )
    suffix = re.sub(r"[^A-Z0-9]", "_", agent.upper())
    for key in (
        "CLAW_ROYALE_API_KEY",
        "CLAW_ROYALE_ERC8004_ID",
        "CERBERUS_AGENT_EOA_PRIVATE_KEY",
        "CERBERUS_AGENT_EOA_ADDRESS",
    ):
        scoped = base.get(f"{key}__{suffix}", "")
        if scoped:
            environment[key] = scoped
        else:
            environment.pop(key, None)
    return environment


def lock_down_local_admin(memory_dir: Path) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    destination = memory_dir / "admin_settings.json"
    payload: dict[str, object] = {}
    if destination.is_file():
        try:
            loaded = json.loads(destination.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        except (OSError, ValueError):
            payload = {}
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        settings = {}
    settings.update(
        {
            "trust_private_network_admin": False,
            "render_env_permissions": False,
            "prefer_existing_env_secrets": True,
        }
    )
    payload["settings"] = settings
    handle, temporary = tempfile.mkstemp(prefix=".admin-settings-", dir=memory_dir)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, destination)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_status(url: str, authorization: str = "") -> int:
    request = urllib.request.Request(
        url,
        data=b'{"state":{}}',
        method="POST",
        headers={
            "Content-Type": "application/json",
            **({"Authorization": authorization} if authorization else {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except (OSError, urllib.error.URLError):
        return 0


def wait_for_core(health_url: str, tick_url: str) -> None:
    for _attempt in range(30):
        try:
            with urllib.request.urlopen(health_url, timeout=3) as response:
                healthy = int(response.status) == 200
        except (OSError, urllib.error.URLError):
            healthy = False
        if healthy and get_status(tick_url) == 401 and get_status(
            tick_url, "Bearer intentionally-wrong"
        ) == 401:
            return
        time.sleep(1)
    raise SystemExit("CERBERUS core did not become healthy with protected local auth")


def stop_children(signum: int, _frame: object) -> None:
    STOPPING.set()
    for child in reversed(CHILDREN):
        if child.poll() is None:
            child.send_signal(signum)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    external_port = parse_port("PORT", "10000")
    core_port = parse_port("CERBERUS_CORE_PORT", "10001")
    if external_port == core_port:
        raise SystemExit("PORT and CERBERUS_CORE_PORT must be different")
    token = os.getenv("CERBERUS_HTTP_TOKEN", "").strip()
    if not token:
        raise SystemExit("CERBERUS_HTTP_TOKEN is required for the Render deployment")

    memory_dir = Path(os.getenv("CERBERUS_MEMORY_DIR", "/var/data/cerberus"))
    lock_down_local_admin(memory_dir)

    core_environment = os.environ.copy()
    core_environment.update(
        {
            "PORT": str(core_port),
            "CERBERUS_BIND_HOST": "127.0.0.1",
            "CLAW_ROYALE_RUNTIME_ENABLED": "false",
        }
    )
    core = subprocess.Popen(
        [sys.executable, str(root / "runtime" / "secure_core_launcher.py")],
        cwd=root,
        env=core_environment,
    )
    CHILDREN.append(core)

    tick_url = f"http://127.0.0.1:{core_port}/tick"
    wait_for_core(f"http://127.0.0.1:{core_port}/healthz", tick_url)

    proxy = ThreadedProxy(("0.0.0.0", external_port), core_port, token)
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()

    if os.getenv("CLAWROYALE_PLUGIN_ENABLED", "").strip().lower() in TRUE_VALUES:
        for agent in agent_ids():
            plugin_environment = agent_environment(dict(os.environ), agent, memory_dir, tick_url)
            plugin = subprocess.Popen(
                [sys.executable, str(root / "runtime" / "claw_worker_launcher.py")],
                cwd=root,
                env=plugin_environment,
            )
            CHILDREN.append(plugin)

    signal.signal(signal.SIGTERM, stop_children)
    signal.signal(signal.SIGINT, stop_children)

    exit_code = 0
    try:
        while not STOPPING.is_set():
            for child in CHILDREN:
                code = child.poll()
                if code is not None:
                    exit_code = int(code or 1)
                    STOPPING.set()
                    break
            time.sleep(1)
    finally:
        proxy.shutdown()
        proxy.server_close()
        stop_children(signal.SIGTERM, None)
        deadline = time.time() + 10
        for child in reversed(CHILDREN):
            remaining = max(0.0, deadline - time.time())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
