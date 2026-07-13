"""
Economy Cortex: free-action value, sMoltz/shop/reforge posture, EP thrift.
"""

from __future__ import annotations

from typing import Any

from agent_dossiers import AgentDossierStore
from combat_decider import is_worth_attacking, target_in_attack_range
from cortex_types import CortexResult, action
from free_action_abuse import weapon_bonus_for_item
from turn_state_model import TurnState


VALUABLE_ITEM_TERMS = (
    "relic",
    "pack",
    "moltz",
    "smoltz",
    "medkit",
    "bandage",
)


class EconomyCortex:
    name = "economy"

    def evaluate(self, state: TurnState, context: dict[str, Any]) -> list[CortexResult]:
        results: list[CortexResult] = []
        if len(state.inventory) >= 10:
            return results

        # Loadout growth now outranks bankroll growth: relics/packs improve
        # future win rate, while loose sMoltz is only the next premium entry.
        for item in state.local_ground_items():
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            if any(term in label for term in ("relic", "pack")):
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="prime_directive_loadout_pickup",
                        score=124,
                        risk=1,
                        priority=114,
                        action=action("pickup", itemId=item.get("id")),
                        reason=f"PRIME DIRECTIVE: loadout growth before loose currency: {label}",
                        source_facts=["F|economy.free", "F|economy.loadout", "F|economy.reforge"],
                    )
                )
                return results

        # 1. Look for sMoltz bundles on the ground.
        for item in state.local_ground_items():
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            if any(term in label for term in ("moltz", "smoltz")):
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="prime_directive_moltz_pickup",
                        score=116,
                        risk=1,
                        priority=106,
                        action=action("pickup", itemId=item.get("id")),
                        reason=f"immediate sMoltz acquisition after loadout scan: {label}",
                        source_facts=["F|economy.free", "F|economy.prime_directive"],
                    )
                )
                return results

        # 2. Hunt wounded agents carrying sMoltz (based on dossiers).
        attack_cost = state.action_ep_cost("attack", 1)
        if state.can_take_main_action and state.self.ep >= attack_cost and not state.alert_active and not state.is_low_hp:
            dossiers = context.get("dossiers") or context.get("dossier_store")
            if not dossiers:
                try:
                    dossiers = AgentDossierStore().load()
                except Exception:
                    dossiers = None
            records = getattr(dossiers, "records", {}) if dossiers else {}
            for agent in state.visible_agents:
                if not agent.is_alive or agent.id == state.self.id:
                    continue
                if not target_in_attack_range(state, agent) or not is_worth_attacking(state, agent):
                    continue
                record = records.get(agent.id)
                tendencies = getattr(record, "observed_tendencies", []) if record else []
                if any("collects" in str(t).lower() and "moltz" in str(t).lower() for t in tendencies):
                    if agent.hp and agent.hp <= 40:
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="hunt_moltz_carrier",
                                score=88,
                                risk=max(8, agent.atk * 0.8),
                                priority=84,
                                action=action("attack", targetId=agent.id, targetType="agent"),
                                reason=f"sMoltz carrier opportunity: wounded {agent.name or agent.id[:8]}",
                                source_facts=["F|economy.prime_directive", "D|social.dossiers", "F|combat.range"],
                            )
                        )
                        return results

        # 3. Collect other valuable items (relics/packs).
        for item in state.local_ground_items():
            label = str(item.get("typeId") or item.get("type") or item.get("name") or "").lower()
            if weapon_bonus_for_item(item) > 0:
                continue
            if any(term in label for term in VALUABLE_ITEM_TERMS):
                premium_value = any(term in label for term in ("relic", "pack", "moltz", "smoltz"))
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="collect_free_value",
                        score=78 if premium_value else 52,
                        risk=3,
                        priority=75 if premium_value else 58,
                        action=action("pickup", itemId=item.get("id")),
                        reason=f"free pickup value: {label}",
                        source_facts=["F|action.free", "F|economy.free", "F|economy.reforge"],
                    )
                )
                break

        return results
