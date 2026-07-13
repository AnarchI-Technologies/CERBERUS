"""Small HTTP clients for Hellion identity onboarding."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import requests

from claw_config import CLAW_API_BASE, active_claw_version


AGENTMAIL_API_BASE = "https://api.agentmail.to/v0"
MOLTBOOK_API_BASE = "https://www.moltbook.com/api/v1"


class OnboardingAPIError(RuntimeError):
    def __init__(self, service: str, status: int, message: str, payload: Any = None):
        self.service = service
        self.status = status
        self.payload = payload
        super().__init__(f"{service} API error {status}: {message}")


def _json_or_text(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text[:1000]}


def _unwrap(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"value": payload}
    data = payload.get("data", payload)
    return data if isinstance(data, dict) else {"value": data, "_raw": payload}


class ClawRoyaleClient:
    def __init__(
        self,
        api_key: str = "",
        base_url: str = CLAW_API_BASE,
        onboarding_token: str | None = None,
    ):
        self.api_key = api_key
        self.onboarding_token = onboarding_token or os.getenv("CLAW_ONBOARDING_TOKEN", "")
        self.access_token = ""
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = kwargs.pop("headers", {})
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-Version", active_claw_version())
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        bearer = self.onboarding_token or self.access_token
        if bearer and (path == "/accounts" or not self.api_key):
            headers["Authorization"] = f"Bearer {bearer}"
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 45),
            **kwargs,
        )
        try:
            payload = _json_or_text(response)
        except Exception:
            payload = {"error": "failed_to_parse_response", "status": response.status_code}

        if not 200 <= response.status_code < 300:
            message = payload.get("error", payload) if isinstance(payload, dict) else payload
            raise OnboardingAPIError("claw_royale", response.status_code, str(message), payload)
        return _unwrap(payload)

    def auth_nonce(self) -> dict[str, Any]:
        return self._request("GET", "/auth/nonce")

    def verify_auth(self, message: str, signature: str) -> dict[str, Any]:
        return self._request("POST", "/auth/verify", json={"message": message, "signature": signature})

    def authenticate_wallet(self, private_key: str) -> dict[str, Any]:
        """Sign Claw's SIWE login message and store returned bearer token."""

        try:
            from eth_account import Account  # type: ignore
            from eth_account.messages import encode_defunct  # type: ignore
        except ImportError as exc:
            raise OnboardingAPIError(
                "claw_royale",
                0,
                "eth-account is required for Claw Royale wallet authentication",
            ) from exc

        account = Account.from_key(private_key)
        nonce_data = self.auth_nonce()
        message = build_claw_siwe_message(
            address=account.address,
            domain=str(nonce_data.get("domain") or "www.clawroyale.ai"),
            uri=str(nonce_data.get("uri") or "https://www.clawroyale.ai"),
            chain_id=int(nonce_data.get("chainId") or 612055),
            nonce=str(nonce_data.get("nonce") or ""),
        )
        signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
        signature = signed.signature.hex()
        if not signature.startswith("0x"):
            signature = "0x" + signature
        auth = self.verify_auth(message, signature)
        self.onboarding_token = str(auth.get("onboardingToken") or "")
        self.access_token = str(auth.get("accessToken") or "")
        return {
            **auth,
            "ownerWalletAddress": auth.get("ownerWalletAddress") or account.address,
            "signedWalletAddress": account.address,
        }

    def create_account(self, name: str, wallet_address: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/accounts",
            json={"name": name[:50], "wallet_address": wallet_address},
        )

    def attach_wallet(self, wallet_address: str) -> dict[str, Any]:
        return self._request("PUT", "/accounts/wallet", json={"wallet_address": wallet_address})

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/accounts/me")

    def create_molty_wallet(self, owner_eoa: str) -> dict[str, Any]:
        return self._request("POST", "/create/wallet", json={"ownerEoa": owner_eoa})

    def request_whitelist(self, owner_eoa: str) -> dict[str, Any]:
        return self._request("POST", "/whitelist/request", json={"ownerEoa": owner_eoa})

    def get_identity(self) -> dict[str, Any]:
        return self._request("GET", "/identity")

    def post_identity(self, token_id: int) -> dict[str, Any]:
        return self._request("POST", "/identity", json={"agentId": token_id})

    def delete_identity(self) -> dict[str, Any]:
        return self._request("DELETE", "/identity")

    def join_status(self) -> dict[str, Any]:
        return self._request("GET", "/join/status")

    def waiting_games(self, *, timeout: float = 5.0) -> dict[str, Any]:
        return self._request("GET", "/games", params={"status": "waiting"}, timeout=timeout)

    def loadout(self) -> dict[str, Any]:
        return self._request("GET", "/loadout")

    def set_active_pack(self, pack_instance_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            "/loadout/pack",
            headers={"Idempotency-Key": idempotency_key},
            json={"packInstanceId": pack_instance_id},
        )

    def clear_active_pack(self, idempotency_key: str) -> dict[str, Any]:
        return self._request("DELETE", "/loadout/pack", headers={"Idempotency-Key": idempotency_key})

    def set_relic_slot(self, type_index: int, relic_instance_id: str, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/loadout/slot/{type_index}",
            headers={"Idempotency-Key": idempotency_key},
            json={"relicInstanceId": relic_instance_id},
        )

    def clear_relic_slot(self, type_index: int, idempotency_key: str) -> dict[str, Any]:
        return self._request("DELETE", f"/loadout/slot/{type_index}", headers={"Idempotency-Key": idempotency_key})

    def inventory_relics(self, after_id: str = "", limit: int = 50) -> dict[str, Any]:
        params = {"limit": limit}
        if after_id:
            params["afterId"] = after_id
        return self._request("GET", "/inventory/relics", params=params)

    def inventory_packs(self, after_id: str = "", limit: int = 50) -> dict[str, Any]:
        params = {"limit": limit}
        if after_id:
            params["afterId"] = after_id
        return self._request("GET", "/inventory/packs", params=params)

    def shop_listings(self) -> dict[str, Any]:
        return self._request("GET", "/shop/listings")

    def purchase_shop_listing(self, listing_id: str, quantity: int, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/shop/purchase",
            headers={"Idempotency-Key": idempotency_key},
            json={"listingId": listing_id, "quantity": max(1, int(quantity))},
        )

    def reforge_relic(self, relic_instance_id: str, item_key: str, idempotency_key: str) -> dict[str, Any]:
        return self._request(
            "POST",
            "/reforge",
            json={
                "relicInstanceId": relic_instance_id,
                "itemKey": item_key,
                "idempotencyKey": idempotency_key,
            },
        )

    def discard_relic(self, relic_instance_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/inventory/relics/{relic_instance_id}")

    def discard_pack(self, pack_instance_id: str) -> dict[str, Any]:
        return self._request("DELETE", f"/inventory/packs/{pack_instance_id}")


def build_claw_siwe_message(
    *,
    address: str,
    domain: str,
    uri: str,
    chain_id: int,
    nonce: str,
    issued_at: str | None = None,
) -> str:
    issued = issued_at or datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        "Sign in with Ethereum to ClawRoyale.\n\n"
        f"URI: {uri}\n"
        "Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {issued}"
    )


class AgentMailClient:
    def __init__(self, api_key: str | None = None, base_url: str = AGENTMAIL_API_BASE):
        self.api_key = api_key or os.getenv("AGENTMAIL_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self._sdk_client = None
        try:
            from agentmail import AgentMail  # type: ignore

            if self.api_key:
                self._sdk_client = AgentMail(api_key=self.api_key)
        except ImportError:
            self._sdk_client = None

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if not self.api_key:
            raise OnboardingAPIError("agentmail", 0, "missing AGENTMAIL_API_KEY")
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            timeout=kwargs.pop("timeout", 45),
            **kwargs,
        )
        payload = _json_or_text(response)
        if not 200 <= response.status_code < 300:
            raise OnboardingAPIError("agentmail", response.status_code, str(payload), payload)
        return _unwrap(payload)

    def create_inbox(self, username: str, display_name: str, client_id: str) -> dict[str, Any]:
        if self._sdk_client is not None:
            try:
                inbox = self._sdk_client.inboxes.create(
                    request={
                        "username": username,
                        "domain": "agentmail.to",
                        "display_name": display_name,
                        "client_id": client_id,
                        "metadata": {"agent": display_name, "system": "cerberus"},
                    }
                )
                return _object_to_dict(inbox)
            except Exception as exc:
                raise OnboardingAPIError("agentmail", 0, str(exc)) from exc

        return self._request(
            "POST",
            "/inboxes",
            json={
                "username": username,
                "domain": "agentmail.to",
                "display_name": display_name,
                "client_id": client_id,
                "metadata": {"agent": display_name, "system": "cerberus"},
            },
        )

    def list_messages(self, inbox_id: str, limit: int = 20) -> dict[str, Any]:
        if self._sdk_client is not None:
            try:
                messages = self._sdk_client.inboxes.messages.list(inbox_id, limit=limit)
                return _object_to_dict(messages)
            except Exception as exc:
                raise OnboardingAPIError("agentmail", 0, str(exc)) from exc
        return self._request("GET", f"/inboxes/{inbox_id}/messages", params={"limit": limit})


def _object_to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {"value": value}


class MoltbookClient:
    def __init__(self, api_key: str = "", base_url: str = MOLTBOOK_API_BASE):
        self.api_key = api_key or os.getenv("MOLTBOOK_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = kwargs.pop("headers", {})
        headers.setdefault("Content-Type", "application/json")
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        response = self.session.request(
            method,
            f"{self.base_url}{path}",
            headers=headers,
            timeout=kwargs.pop("timeout", 45),
            **kwargs,
        )
        payload = _json_or_text(response)
        if not 200 <= response.status_code < 300:
            raise OnboardingAPIError("moltbook", response.status_code, str(payload), payload)
        return _unwrap(payload)

    def register_agent(self, name: str, description: str) -> dict[str, Any]:
        payload = self._request(
            "POST",
            "/agents/register",
            json={"name": name, "description": description},
        )
        agent = payload.get("agent", payload)
        return agent if isinstance(agent, dict) else payload

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/agents/me")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/agents/status")
