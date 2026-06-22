"""Live MoltStation ShellRunners worker for Cerberus."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets

from claw_signing import HeadlessAgentWallet
from env_loader import load_dotenv_file
from memory_system import DEFAULT_MEMORY_DIR, atomic_write_text


ROOT = Path(__file__).resolve().parents[1]
MOLT_API_BASE = "https://api.moltstation.games"
MOLT_WEB_BASE = "https://www.moltstation.games"


def _state_file() -> Path:
    return Path(os.getenv("CERBERUS_MEMORY_DIR") or DEFAULT_MEMORY_DIR) / "moltstation_runtime_status.json"


def _write_state(payload: dict[str, Any]) -> None:
    atomic_write_text(_state_file(), json.dumps(payload, ensure_ascii=True, separators=(",", ":")), encoding="utf-8")


def _read_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        data = {"error": response.text[:1000]}
    return data if isinstance(data, dict) else {"value": data}


def _clean_slug(value: str) -> str:
    slug = "".join(ch for ch in (value or "").strip().lower() if ch.isalnum() or ch in {"-", "_"})
    return slug or "shellrunners"


def _siwe_message(address: str, nonce_payload: dict[str, Any]) -> str:
    domain = str(nonce_payload.get("domain") or "moltstation.games").strip()
    uri = str(nonce_payload.get("uri") or "https://moltstation.games").strip()
    chain_id = int(nonce_payload.get("chainId") or 8453)
    nonce = str(nonce_payload.get("nonce") or "").strip()
    issued = str(nonce_payload.get("issuedAt") or nonce_payload.get("issued_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        "Sign in with Ethereum to MoltStation.\n\n"
        f"URI: {uri}\n"
        "Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued}"
    )


@dataclass
class MoltstationConfig:
    enabled: bool
    game_slug: str
    api_key: str
    private_key: str
    wallet_address: str
    shellrunner_contract: str


class MoltstationClient:
    def __init__(self, cfg: MoltstationConfig):
        self.cfg = cfg
        self.session = requests.Session()
        self.access_token = ""
        self.refresh_token = ""
        self.game_slug = _clean_slug(cfg.game_slug)
        self.wallet = HeadlessAgentWallet(cfg.private_key)
        self.shellrunner_contract = cfg.shellrunner_contract.strip()

    @property
    def headers(self) -> dict[str, str]:
        out = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            out["X-API-Key"] = self.cfg.api_key
        return out

    def _request(self, method: str, path: str, *, auth: bool = False, timeout: int = 30, **kwargs: Any) -> dict[str, Any]:
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", {}))
        if auth and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        response = self.session.request(method, f"{MOLT_API_BASE}{path}", headers=headers, timeout=timeout, **kwargs)
        payload = _read_json(response)
        if not response.ok:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {payload.get('error') or payload}")
        return payload

    def auth(self) -> dict[str, Any]:
        nonce = self._request("POST", f"/api/games/{self.game_slug}/auth/nonce")
        message = _siwe_message(self.wallet.address, nonce)
        signed = self.wallet.sign_challenge(type("C", (), {"mode": "plain_message", "typed_data": {}, "plain_message": message})())
        verify = self._request(
            "POST",
            f"/api/games/{self.game_slug}/auth/verify",
            json={"message": message, "signature": signed["signature"]},
        )
        self.access_token = str(verify.get("accessToken") or "")
        self.refresh_token = str(verify.get("refreshToken") or "")
        return verify

    def session_start(self) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/games/{self.game_slug}/sessions/start",
            auth=True,
            json={"source": "agent_api"},
        )

    def play_token(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/games/{self.game_slug}/sessions/{session_id}/play-token", auth=True)

    def snapshot_prepare(self, session_id: str) -> dict[str, Any]:
        return self._request("POST", f"/api/rewards/snapshot", auth=True, json={"gameSlug": self.game_slug, "sessionId": session_id})

    def readiness(self) -> dict[str, Any]:
        return self._request("POST", "/api/rewards/readiness", auth=True, json={"gameSlug": self.game_slug})

    def payout_history(self) -> dict[str, Any]:
        return self._request("POST", "/api/rewards/payout-history", auth=True, json={"gameSlug": self.game_slug, "includeAllGames": True})


def load_config() -> MoltstationConfig:
    load_dotenv_file()
    load_dotenv_file(ROOT / ".env.moltstation")
    return MoltstationConfig(
        enabled=os.getenv("MOLTSTATION_RUNTIME_ENABLED", "").strip().lower() in {"1", "true", "yes", "on"},
        game_slug=os.getenv("MOLTSTATION_GAME_SLUG", "shellrunners"),
        api_key=os.getenv("MOLTSTATION_API_KEY", "").strip(),
        private_key=os.getenv("MOLTSTATION_AGENT_PRIVATE_KEY", os.getenv("CERBERUS_AGENT_EOA_PRIVATE_KEY", "")).strip(),
        wallet_address=os.getenv("MOLTSTATION_AGENT_WALLET", os.getenv("CERBERUS_AGENT_EOA_ADDRESS", "")).strip(),
        shellrunner_contract=os.getenv("MOLTSTATION_SHELLRUNNERS_CONTRACT", "").strip(),
    )


def choose_dir(frame: dict[str, Any]) -> str:
    pawn = frame.get("pawn") if isinstance(frame.get("pawn"), dict) else {}
    x = int(pawn.get("x") or 0)
    items = frame.get("entities") if isinstance(frame.get("entities"), list) else []
    obstacles = [item for item in items if isinstance(item, dict) and str(item.get("k") or "") == "obstacle"]
    if any(abs(int(item.get("x") or 0) - x) < 40 for item in obstacles):
        return "left" if x >= 960 else "right"
    collectibles = [item for item in items if isinstance(item, dict) and str(item.get("k") or "") in {"collectible", "powerup"}]
    if collectibles:
        target = min(collectibles, key=lambda item: abs(int(item.get("x") or 0) - x))
        tx = int(target.get("x") or 0)
        if tx < x - 14:
            return "left"
        if tx > x + 14:
            return "right"
    return "none"


async def _play_loop(client: MoltstationClient, session_id: str, play_token: str) -> None:
    ws_url = f"wss://api.moltstation.games/ws/{client.game_slug}/play?sessionId={session_id}"
    _write_state({"state": "connecting", "game_slug": client.game_slug, "session_id": session_id, "updated_at": int(time.time())})
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"t": "auth", "token": play_token}))
        _write_state({"state": "live", "game_slug": client.game_slug, "session_id": session_id, "updated_at": int(time.time())})
        async for raw in ws:
            payload = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8"))
            if not isinstance(payload, dict):
                continue
            if payload.get("t") == "hello":
                continue
            if payload.get("t") != "frame":
                continue
            frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
            phase = str(frame.get("phase") or "")
            dir_choice = choose_dir(frame)
            await ws.send(json.dumps({"t": "input", "dir": dir_choice}))
            _write_state(
                {
                    "state": "playing" if phase != "ended" else "ended",
                    "game_slug": client.game_slug,
                    "session_id": session_id,
                    "phase": phase,
                    "score": frame.get("score", {}),
                    "lives": frame.get("lives"),
                    "hunger": frame.get("hunger"),
                    "dir": dir_choice,
                    "updated_at": int(time.time()),
                }
            )
            if phase == "ended":
                break


async def run_forever() -> None:
    cfg = load_config()
    if not cfg.enabled:
        _write_state({"state": "disabled", "reason": "MOLTSTATION_RUNTIME_ENABLED is not set", "updated_at": int(time.time())})
        return
    if not cfg.private_key or not cfg.wallet_address:
        _write_state({"state": "blocked", "reason": "missing wallet credentials", "updated_at": int(time.time())})
        return
    client = MoltstationClient(cfg)
    backoff = 5
    while True:
        try:
            auth = client.auth()
            start = client.session_start()
            session_id = str(start.get("sessionId") or start.get("session_id") or "")
            if not session_id:
                raise RuntimeError(f"missing session id in start response: {start}")
            token = client.play_token(session_id)
            play_token = str(token.get("playToken") or token.get("token") or "")
            if not play_token:
                raise RuntimeError(f"missing play token in response: {token}")
            await _play_loop(client, session_id, play_token)
            client.snapshot_prepare(session_id)
            try:
                ready = client.readiness()
            except Exception as exc:
                ready = {"ok": False, "error": str(exc)}
            try:
                history = client.payout_history()
            except Exception as exc:
                history = {"ok": False, "error": str(exc)}
            _write_state(
                {
                    "state": "cycle_complete",
                    "game_slug": client.game_slug,
                    "shellrunner_contract": client.shellrunner_contract,
                    "session_id": session_id,
                    "auth_ok": bool(auth.get("accessToken")),
                    "readiness": ready,
                    "payout_history": history,
                    "updated_at": int(time.time()),
                }
            )
            backoff = 5
        except Exception as exc:
            _write_state({"state": "reconnecting", "error": str(exc)[:500], "game_slug": client.game_slug, "shellrunner_contract": client.shellrunner_contract, "updated_at": int(time.time())})
            await asyncio.sleep(backoff)
            backoff = min(60, backoff * 2)
