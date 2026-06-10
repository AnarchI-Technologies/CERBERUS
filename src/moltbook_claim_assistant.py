"""Moltbook claim helper for Hellion.

The agent can prepare and monitor the claim, but Moltbook's public flow still
requires the human owner to complete the external ownership step.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT / "src", ROOT / "data"):
    path = str(folder)
    if path not in sys.path:
        sys.path.insert(0, path)

from identity_vault import IdentityVault  # noqa: E402
from onboarding_clients import AgentMailClient, MoltbookClient, OnboardingAPIError  # noqa: E402
from x_oauth import post_tweet  # noqa: E402


URL_RE = re.compile(r"https://www\.moltbook\.com/[^\s\"'<>]+", re.I)
CODE_RE = re.compile(r"\b[A-Z0-9][A-Z0-9_-]{5,}\b", re.I)


def _walk_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_walk_strings(item))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            out.extend(_walk_strings(item))
        return out
    return [str(value)]


def extract_moltbook_claims(payload: Any) -> dict[str, list[str]]:
    text = "\n".join(_walk_strings(payload))
    urls = sorted(set(URL_RE.findall(text)))
    claim_urls = [url for url in urls if "claim" in url.lower()]
    codes = [
        code
        for code in sorted(set(CODE_RE.findall(text)))
        if not code.lower().startswith(("https", "moltbook", "agentmail"))
    ]
    return {"claim_urls": claim_urls, "urls": urls, "codes": codes[:20]}


def load_identity() -> dict[str, Any]:
    return IdentityVault().load().data


def stored_claim(identity: dict[str, Any]) -> dict[str, str]:
    moltbook = identity.get("moltbook", {})
    return {
        "claim_url": str(moltbook.get("claim_url") or ""),
        "verification_code": str(moltbook.get("verification_code") or ""),
        "status": str(moltbook.get("status") or ""),
    }


def inbox_claim(identity: dict[str, Any], limit: int = 20) -> dict[str, list[str]]:
    mail = identity.get("agentmail", {})
    inbox_id = mail.get("inbox_id") or mail.get("email")
    api_key = mail.get("api_key") or ""
    if not inbox_id:
        return {"claim_urls": [], "urls": [], "codes": []}
    messages = AgentMailClient(api_key=api_key).list_messages(str(inbox_id), limit=limit)
    return extract_moltbook_claims(messages)


def claim_status(identity: dict[str, Any]) -> dict[str, Any]:
    api_key = identity.get("moltbook", {}).get("api_key", "")
    if not api_key:
        return {"ok": False, "reason": "missing Moltbook API key in identity vault"}
    try:
        return MoltbookClient(api_key=api_key).status()
    except OnboardingAPIError as exc:
        return {"ok": False, "reason": str(exc)}


def print_claim_packet(identity: dict[str, Any], *, include_inbox: bool = False) -> None:
    stored = stored_claim(identity)
    inbox = inbox_claim(identity) if include_inbox else {"claim_urls": [], "urls": [], "codes": []}
    claim_url = stored["claim_url"] or (inbox["claim_urls"][0] if inbox["claim_urls"] else "")
    verification_code = stored["verification_code"] or (inbox["codes"][0] if inbox["codes"] else "")

    print("Moltbook claim packet")
    print(f"- Agent: {identity.get('public_name', '')}")
    print(f"- Claim URL: {claim_url or '(not found)'}")
    print(f"- Verification code: {verification_code or '(not found)'}")
    print(f"- Stored status: {stored['status'] or '(unknown)'}")
    print()
    print("Human-owner step still required by Moltbook:")
    print("1. Open the claim URL.")
    print("2. Verify email if Moltbook asks for it.")
    print("3. Post the verification code on X/Twitter as instructed by Moltbook.")
    print("4. Re-open/confirm the claim page.")
    print("5. Run this helper with `status` to confirm the agent is claimed.")


def verification_text(identity: dict[str, Any], *, include_inbox: bool = False) -> str:
    stored = stored_claim(identity)
    inbox = inbox_claim(identity) if include_inbox else {"claim_urls": [], "urls": [], "codes": []}
    verification_code = stored["verification_code"] or (inbox["codes"][0] if inbox["codes"] else "")
    if not verification_code:
        raise RuntimeError("No Moltbook verification code found.")
    return f"Verifying ownership of {identity.get('public_name', 'my AI agent')} on Moltbook: {verification_code}"


def post_verification(identity: dict[str, Any], *, include_inbox: bool = False) -> dict[str, Any]:
    text = verification_text(identity, include_inbox=include_inbox)
    return post_tweet(text, identity=identity)


def claim_with_x_oauth(*, include_inbox: bool = False, check_status: bool = True) -> dict[str, Any]:
    vault = IdentityVault().load()
    vault.require_pin_ready()
    identity = vault.data
    text = verification_text(identity, include_inbox=include_inbox)
    tweet = post_tweet(text, identity=identity)
    tweet_id = ""
    if isinstance(tweet, dict):
        data = tweet.get("data", {})
        if isinstance(data, dict):
            tweet_id = str(data.get("id") or "")

    moltbook = identity.setdefault("moltbook", {})
    moltbook["status"] = "x_verification_posted"
    moltbook["verification_tweet_text"] = text
    if tweet_id:
        moltbook["verification_tweet_id"] = tweet_id
        moltbook["verification_tweet_url"] = f"https://twitter.com/i/web/status/{tweet_id}"
    moltbook["claim_attempt"] = {"tweet": tweet}
    vault.event("Posted Moltbook verification via delegated X OAuth", tweet_id=tweet_id)

    status: dict[str, Any] = {}
    if check_status:
        status = claim_status(identity)
        moltbook["claim_status_after_x"] = status
        if status.get("ok") and str(status).lower().find("claimed") >= 0:
            moltbook["status"] = "claimed"
    vault.save()
    return {
        "ok": True,
        "posted": True,
        "tweet_id": tweet_id,
        "tweet_url": moltbook.get("verification_tweet_url", ""),
        "moltbook_status": status,
    }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="Help Hellion complete Moltbook claim workflow")
    parser.add_argument("command", choices=("packet", "inbox", "status", "tweet", "claim"), nargs="?", default="packet")
    parser.add_argument("--include-inbox", action="store_true", help="Read AgentMail for claim links/codes")
    parser.add_argument("--no-status", action="store_true", help="Skip Moltbook status check after claim tweet")
    args = parser.parse_args()
    identity = load_identity()

    if args.command == "packet":
        print_claim_packet(identity, include_inbox=args.include_inbox)
        return 0
    if args.command == "inbox":
        print(extract_moltbook_claims(inbox_claim(identity)))
        return 0
    if args.command == "status":
        print(claim_status(identity))
        return 0
    if args.command == "tweet":
        print(post_verification(identity, include_inbox=args.include_inbox))
        return 0
    if args.command == "claim":
        print(claim_with_x_oauth(include_inbox=args.include_inbox, check_status=not args.no_status))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
