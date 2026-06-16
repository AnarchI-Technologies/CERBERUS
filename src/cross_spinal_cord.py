"""
CrossSpinalCord: A generic runtime for any Cross Mainnet game.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from cortex_types import Cortex
from core_loop import normalize_action
from decision_engine import make_plan


class GameRuntime(Protocol):
    """Interface for game-specific websocket/API connectors."""

    def get_snapshot(self) -> dict[str, Any]: ...
    def send_action(self, action: dict[str, Any]) -> None: ...


def _process_moltybook(effect: dict[str, Any]) -> dict[str, Any]:
    """Route to MoltyBook client to publish drafts and follows."""
    try:
        from identity_vault import IdentityVault
        from moltybook_client import MoltyBookClient

        vault = IdentityVault().load()
        api_key = vault.data.get("moltbook", {}).get("api_key")
        if not api_key:
            return {"ok": False, "type": effect.get("type"), "reason": "missing_moltbook_api_key"}

        client = MoltyBookClient(api_key=api_key, enabled=True)
        if effect.get("type") == "moltybook_draft":
            return client.post_draft(effect)
        if effect.get("type") == "moltybook_follow":
            return client.follow(effect)
        return {"ok": False, "type": effect.get("type"), "reason": "unsupported_moltybook_effect"}
    except Exception as exc:
        return {"ok": False, "type": effect.get("type"), "reason": "moltybook_side_effect_failed", "error": str(exc)[:240]}


def _process_forge_swap(effect: dict[str, Any]) -> dict[str, Any]:
    """Route to on-chain swap logic via Crosstoken Forge."""
    try:
        from identity_vault import IdentityVault
        from forge_token_contract import ROUTER_ADDRESS

        vault = IdentityVault().load()
        wallets = vault.data.get("wallets", {})
        agent_pk = wallets.get("agent_eoa", {}).get("private_key")

        if not agent_pk:
            return {"ok": False, "type": "forge_swap", "reason": "missing_agent_private_key"}

        return {
            "ok": False,
            "type": "forge_swap",
            "reason": "not_implemented",
            "router": ROUTER_ADDRESS,
        }
    except Exception as exc:
        return {"ok": False, "type": "forge_swap", "reason": "forge_swap_side_effect_failed", "error": str(exc)[:240]}


SIDE_EFFECT_REGISTRY: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "moltybook_draft": _process_moltybook,
    "moltybook_follow": _process_moltybook,
    "forge_swap": _process_forge_swap,
}


class CrossSpinalCord:
    def __init__(
        self,
        runtime: GameRuntime,
        shared_cortexes: list[Cortex],
        game_specific_cortexes: list[Cortex],
        memory_store: Any,
    ):
        self.runtime = runtime
        self.cortexes = shared_cortexes + game_specific_cortexes
        self.memory = memory_store
        self.last_side_effects: list[dict[str, Any]] = []

    def pulse(self) -> dict[str, Any]:
        """The heartbeat of the agent."""
        # 1. Fetch raw state from the specific game runtime
        raw_state = self.runtime.get_snapshot()

        # 2. Run the shared Decision Engine
        # We reuse make_plan but pass the full combined cortex list
        plan = make_plan(
            state=raw_state,
            memory_store=self.memory,
            cortexes=self.cortexes
        )

        action = plan.get("action")
        if action:
            # 3. Normalize and execute
            final_action = normalize_action(action)
            self.runtime.send_action(final_action)

            # 4. Handle side effects (Social, Trading, etc.)
            self.last_side_effects = self._handle_side_effects(plan.get("side_effects", []))
            plan["side_effect_results"] = self.last_side_effects
        return plan

    def _handle_side_effects(self, effects: list[dict[str, Any]]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for effect in effects:
            processor = SIDE_EFFECT_REGISTRY.get(effect.get("type", ""))
            if processor:
                results.append(processor(effect))
            else:
                results.append({"ok": False, "type": effect.get("type", ""), "reason": "unregistered_side_effect"})
        return results
