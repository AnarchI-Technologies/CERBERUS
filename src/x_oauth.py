"""X OAuth 2.0 helper for delegated Hellion posting."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from identity_vault import IdentityVault  # noqa: E402
from env_loader import env_value, hydrate_env  # noqa: E402
from onboarding_clients import AgentMailClient  # noqa: E402


AUTH_URL = "https://twitter.com/i/oauth2/authorize"
TOKEN_URL = "https://api.twitter.com/2/oauth2/token"
TWEET_URL = "https://api.twitter.com/2/tweets"
DEFAULT_SCOPES = ("tweet.read", "tweet.write", "users.read", "offline.access")
LATEST_AUTH_URL_FILE = ROOT / "x_auth_url.txt"
LATEST_OAUTH_SESSION_FILE = ROOT / "x_oauth_session.json"


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "OAuthCallbackServer"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.callback_params = {key: values[0] for key, values in params.items() if values}
        body = b"X authorization received. You can return to the terminal."
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


class OAuthCallbackServer(HTTPServer):
    callback_params: dict[str, str]


def pkce_pair() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def env_config() -> dict[str, str]:
    hydrate_env(("X_CLIENT_ID", "X_CLIENT_SECRET", "X_REDIRECT_URI"))
    return {
        "client_id": env_value("X_CLIENT_ID"),
        "client_secret": env_value("X_CLIENT_SECRET"),
        "redirect_uri": env_value("X_REDIRECT_URI", "http://127.0.0.1:8765/x/callback"),
    }


def authorization_url(*, state: str, challenge: str, config: dict[str, str]) -> str:
    params = {
        "response_type": "code",
        "client_id": config["client_id"],
        "redirect_uri": config["redirect_uri"],
        "scope": " ".join(DEFAULT_SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def wait_for_callback(
    redirect_uri: str,
    *,
    expected_state: str = "",
    timeout_seconds: int = 180,
) -> dict[str, str]:
    parsed = urllib.parse.urlparse(redirect_uri)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8765
    server = OAuthCallbackServer((host, port), OAuthCallbackHandler)
    server.timeout = 1
    server.callback_params = {}
    deadline = time.time() + timeout_seconds
    print(f"Listening for X callback on {redirect_uri}")
    while time.time() < deadline:
        server.handle_request()
        if not server.callback_params:
            continue
        if expected_state and server.callback_params.get("state") != expected_state:
            print("Ignored stale X callback with mismatched state; waiting for the current authorization.")
            server.callback_params = {}
            continue
        break
    if not server.callback_params:
        raise TimeoutError("Timed out waiting for X OAuth callback.")
    return server.callback_params


def parse_callback_url(callback_url: str) -> dict[str, str]:
    parsed = urllib.parse.urlparse(callback_url.strip())
    raw_params = parsed.query or parsed.fragment
    params = urllib.parse.parse_qs(raw_params)
    return {key: values[0] for key, values in params.items() if values}


def exchange_code(*, code: str, verifier: str, config: dict[str, str]) -> dict[str, Any]:
    auth = None
    if config.get("client_secret"):
        raw = f"{config['client_id']}:{config['client_secret']}".encode("utf-8")
        auth = "Basic " + base64.b64encode(raw).decode("ascii")
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if auth:
        headers["Authorization"] = auth
    response = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "grant_type": "authorization_code",
            "client_id": config["client_id"],
            "redirect_uri": config["redirect_uri"],
            "code_verifier": verifier,
        },
        headers=headers,
        timeout=45,
    )
    if not 200 <= response.status_code < 300:
        detail = response.text[:500]
        if response.status_code == 400 and "authorization code was invalid" in detail:
            detail += (
                " | Use the newest x_auth_url.txt link only once; X authorization codes are single-use "
                "and must be exchanged with the same saved PKCE session that generated the URL."
            )
        raise RuntimeError(f"X token exchange failed {response.status_code}: {detail}")
    return response.json()


def store_tokens(tokens: dict[str, Any], config: dict[str, str]) -> dict[str, Any]:
    vault = IdentityVault().load()
    vault.require_pin_ready()
    vault.data.setdefault("x_account", {}).update(
        {
            "client_id": config["client_id"],
            "client_secret": config.get("client_secret", ""),
            "redirect_uri": config["redirect_uri"],
            "access_token": tokens.get("access_token", ""),
            "refresh_token": tokens.get("refresh_token", ""),
            "scope": tokens.get("scope", " ".join(DEFAULT_SCOPES)),
            "token_type": tokens.get("token_type", "bearer"),
            "expires_in": tokens.get("expires_in", 0),
            "stored_at": int(time.time()),
        }
    )
    vault.event("Stored delegated X OAuth tokens")
    vault.save()
    return vault.public_summary().get("x_account", {})


def authorize() -> dict[str, Any]:
    config = env_config()
    if not config["client_id"]:
        raise RuntimeError("Set X_CLIENT_ID before authorizing X.")
    if not config["redirect_uri"].startswith("http://127.0.0.1:"):
        print(f"Using redirect URI: {config['redirect_uri']}")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorization_url(state=state, challenge=challenge, config=config)
    save_latest_auth_url(url)
    print("Opening X authorization URL...")
    print(url)
    webbrowser.open(url)
    params = wait_for_callback(config["redirect_uri"], expected_state=state)
    if params.get("error"):
        raise RuntimeError(f"X OAuth error: {params}")
    tokens = exchange_code(code=params["code"], verifier=verifier, config=config)
    return store_tokens(tokens, config)


def save_latest_auth_url(url: str) -> Path:
    LATEST_AUTH_URL_FILE.write_text(url + "\n", encoding="utf-8")
    return LATEST_AUTH_URL_FILE


def save_oauth_session(*, state: str, verifier: str, config: dict[str, str]) -> Path:
    LATEST_OAUTH_SESSION_FILE.write_text(
        json.dumps(
            {
                "state": state,
                "verifier": verifier,
                "client_id": config["client_id"],
                "redirect_uri": config["redirect_uri"],
                "created_at": int(time.time()),
            },
            ensure_ascii=True,
            indent=2,
        ),
        encoding="utf-8",
    )
    return LATEST_OAUTH_SESSION_FILE


def load_oauth_session() -> dict[str, Any]:
    if not LATEST_OAUTH_SESSION_FILE.exists():
        raise RuntimeError("No saved X OAuth session found. Run `python src\\x_oauth.py authorize --manual-callback` first.")
    return json.loads(LATEST_OAUTH_SESSION_FILE.read_text(encoding="utf-8"))


def oauth_session_age_seconds(session: dict[str, Any]) -> int:
    try:
        return max(0, int(time.time()) - int(session.get("created_at") or 0))
    except (TypeError, ValueError):
        return 0


def send_authorization_email(url: str, *, to_email: str = "") -> None:
    recipient = to_email or os.getenv("X_AUTH_EMAIL_TO", "") or os.getenv("AGENTMAIL_TEST_TO", "")
    if not recipient:
        raise RuntimeError("Set X_AUTH_EMAIL_TO or AGENTMAIL_TEST_TO before emailing the X auth URL.")
    identity = IdentityVault().load().data
    mail = identity.get("agentmail", {})
    inbox_id = str(mail.get("inbox_id") or mail.get("email") or "")
    api_key = str(mail.get("api_key") or os.getenv("AGENTMAIL_API_KEY", ""))
    if not inbox_id:
        raise RuntimeError("No AgentMail inbox stored in identity vault.")
    client = AgentMailClient(api_key=api_key)
    if client._sdk_client is not None:
        client._sdk_client.inboxes.messages.send(
            inbox_id,
            to=recipient,
            subject="Hellion X authorization link",
            text=(
                "Open this X authorization URL to delegate posting access to Hellion:\n\n"
                f"{url}\n\n"
                "Only use the newest link. Older links may be ignored by the local callback listener."
            ),
        )
        return
    response = requests.post(
        f"{client.base_url}/inboxes/{inbox_id}/messages",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "to": recipient,
            "subject": "Hellion X authorization link",
            "text": (
                "Open this X authorization URL to delegate posting access to Hellion:\n\n"
                f"{url}\n\n"
                "Only use the newest link. Older links may be ignored by the local callback listener."
            ),
        },
        timeout=45,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"AgentMail auth URL email failed {response.status_code}: {response.text[:500]}")


def authorize_with_optional_email(
    *,
    email_url: bool = False,
    to_email: str = "",
    manual_callback: bool = False,
    open_browser: bool = True,
) -> dict[str, Any]:
    config = env_config()
    if not config["client_id"]:
        raise RuntimeError("Set X_CLIENT_ID before authorizing X.")
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(24)
    url = authorization_url(state=state, challenge=challenge, config=config)
    save_latest_auth_url(url)
    save_oauth_session(state=state, verifier=verifier, config=config)
    print("Opening X authorization URL...")
    print(url)
    print(f"Saved X authorization URL to {LATEST_AUTH_URL_FILE}")
    print(f"Saved X OAuth session to {LATEST_OAUTH_SESSION_FILE}")
    if email_url:
        try:
            send_authorization_email(url, to_email=to_email)
            print("Sent X authorization URL by email.")
        except Exception as exc:
            print(f"Warning: could not email X authorization URL: {exc}")
            print("Continuing with browser/local callback. Use the printed URL or x_auth_url.txt.")
    if open_browser:
        webbrowser.open(url)
    if manual_callback:
        print()
        print("Manual callback mode:")
        print("1. Open the newest auth URL on the device/browser where X login works.")
        print("2. After approval, copy the full redirected URL from the address bar.")
        print("3. Paste it below. It should contain code=... and state=...")
        callback_url = input("Paste redirected callback URL: ").strip()
        params = parse_callback_url(callback_url)
        if params.get("state") != state:
            raise RuntimeError("X OAuth state mismatch. Make sure you pasted the redirect from the newest auth URL.")
    else:
        params = wait_for_callback(config["redirect_uri"], expected_state=state)
    if params.get("error"):
        raise RuntimeError(f"X OAuth error: {params}")
    tokens = exchange_code(code=params["code"], verifier=verifier, config=config)
    return store_tokens(tokens, config)


def exchange_callback_url(callback_url: str) -> dict[str, Any]:
    config = env_config()
    session = load_oauth_session()
    age = oauth_session_age_seconds(session)
    if age > 900:
        raise RuntimeError(
            f"Saved X OAuth session is {age} seconds old. Generate a fresh auth URL before exchanging a callback."
        )
    params = parse_callback_url(callback_url)
    if params.get("error"):
        raise RuntimeError(f"X OAuth error in callback URL: {params}")
    if not params.get("code"):
        raise RuntimeError("Callback URL does not contain a code= parameter.")
    if params.get("state") != session.get("state"):
        raise RuntimeError(
            "X OAuth state mismatch. Paste the redirect generated from the newest x_auth_url.txt."
        )
    if session.get("redirect_uri") and config["redirect_uri"] != session["redirect_uri"]:
        raise RuntimeError(
            f"X_REDIRECT_URI changed since auth URL generation: current={config['redirect_uri']} saved={session['redirect_uri']}"
        )
    tokens = exchange_code(code=params["code"], verifier=session["verifier"], config=config)
    return store_tokens(tokens, config)


def post_tweet(text: str, *, identity: dict[str, Any] | None = None) -> dict[str, Any]:
    if identity is None:
        identity = IdentityVault().load().data
    access_token = identity.get("x_account", {}).get("access_token", "")
    if not access_token:
        raise RuntimeError("No X access token in identity vault. Run x_oauth.py authorize first.")
    response = requests.post(
        TWEET_URL,
        json={"text": text},
        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
        timeout=45,
    )
    payload = response.json() if response.text else {}
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"X post failed {response.status_code}: {json.dumps(payload)[:500]}")
    return payload


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Authorize/store X OAuth tokens or post a delegated tweet")
    sub = parser.add_subparsers(dest="cmd", required=True)
    auth = sub.add_parser("authorize")
    auth.add_argument("--email-url", action="store_true", help="Email the generated X auth URL via AgentMail")
    auth.add_argument("--to", default="", help="Recipient for --email-url")
    auth.add_argument("--manual-callback", action="store_true", help="Paste the final redirected callback URL manually")
    auth.add_argument("--no-open", action="store_true", help="Print/save the auth URL without opening a local browser")
    post = sub.add_parser("post")
    post.add_argument("text")
    exchange = sub.add_parser("exchange-url")
    exchange.add_argument("callback_url", nargs="?", default="", help="Full redirected callback URL containing code and state")
    args = parser.parse_args()
    if args.cmd == "authorize":
        print(
            authorize_with_optional_email(
                email_url=args.email_url,
                to_email=args.to,
                manual_callback=args.manual_callback,
                open_browser=not args.no_open,
            )
        )
        return 0
    if args.cmd == "post":
        print(post_tweet(args.text))
        return 0
    if args.cmd == "exchange-url":
        callback_url = args.callback_url or input("Paste redirected callback URL: ").strip()
        print(exchange_callback_url(callback_url))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
