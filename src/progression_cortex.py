"""
Progression Cortex: Ruins, Relics, Packs, settlement retention.
"""

from __future__ import annotations

from cortex_types import CortexResult, action, rest_action
from risk_engine import progression_value_at_risk
from turn_state_model import TurnState


class ProgressionCortex:
    name = "progression"

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        region = state.current_region
        terrain = region.terrain.lower()
        region_name = region.name.lower()
        looks_like_ruin = "ruin" in terrain or "ruin" in region_name or bool(state.ruins)

        if (
            looks_like_ruin
            and state.can_take_main_action
            and state.self.ep >= 1
            and not state.alert_active
            and not state.visible_agents
            and not state.is_low_hp
            and not state.is_in_death_zone
            and not state.is_pending_death_zone
        ):
            future_alert = state.alert_gauge + 2
            risk = (10 if future_alert < 10 else 45) + progression_value_at_risk(state)
            score = 72 if future_alert < 10 else 48
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="explore_ruin",
                    score=score,
                    risk=risk,
                    priority=72,
                    action=action("explore"),
                    reason=f"ruin progression available; alert {state.alert_gauge}->~{future_alert}",
                    source_facts=[
                        "F|progression.ruin",
                        "F|safety.alert",
                        "F|progression.settlement",
                    ],
                )
            )

        carried_relics = [
            item
            for item in state.inventory
            if "relic" in str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
        ]
        carried_packs = [
            item
            for item in state.inventory
            if "pack" in str(item.get("type") or item.get("typeId") or item.get("name") or "").lower()
        ]
        if carried_relics or carried_packs:
            cargo_risk = max(20 if state.self.hp < 50 else 5, progression_value_at_risk(state))
            cargo_count = len(carried_relics) + len(carried_packs)
            heal = state.best_heal_item()
            if heal and (state.is_low_hp or cargo_risk >= 45):
                results.append(
                    CortexResult(
                        cortex=self.name,
                        intent="preserve_cargo_heal",
                        score=96 + cargo_count * 4,
                        risk=0,
                        priority=96,
                        veto=True,
                        action=action("use_item", itemId=heal.get("id")),
                        reason=f"carrying relic/pack cargo under risk {cargo_risk}; heal before greed",
                        source_facts=["F|progression.inventory", "F|items.recovery", "F|safety.survival"],
                )
            )
            if (
                (state.is_in_death_zone or state.is_pending_death_zone or state.alert_active or cargo_risk >= 55 or state.self.hp <= 45)
                and state.can_take_main_action
            ):
                safe_regions = state.connected_safe_regions()
                if safe_regions and state.self.ep > 0:
                    target = _best_cargo_escape_region(safe_regions)
                    if target:
                        results.append(
                            CortexResult(
                                cortex=self.name,
                                intent="preserve_cargo_reposition",
                                score=92 + cargo_count * 5,
                                risk=min(cargo_risk, 80),
                                priority=95,
                                veto=True,
                                action=action("move", regionId=target),
                                reason=f"carrying relics={len(carried_relics)} packs={len(carried_packs)}; bank survival by leaving risk {cargo_risk}",
                                source_facts=["F|progression.settlement", "F|safety.movement", "F|progression.inventory"],
                            )
                        )
                elif state.self.ep <= 1 and not state.is_in_death_zone and not state.is_pending_death_zone:
                    results.append(
                        CortexResult(
                            cortex=self.name,
                            intent="preserve_cargo_recover_ep",
                            score=82 + cargo_count * 4,
                            risk=min(cargo_risk, 75),
                            priority=86,
                            action=rest_action("carrying relic/pack cargo; recover EP before unsafe rotation"),
                            reason=f"carrying cargo with no safe EP rotation; recover before risking loadout value {cargo_risk}",
                            source_facts=["F|progression.settlement", "F|progression.inventory", "F|action.rest"],
                        )
                    )
            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="preserve_progression_cargo",
                    score=65 + 5 * len(carried_relics) + 12 * len(carried_packs),
                    risk=cargo_risk,
                    priority=70,
                    action=None,
                    reason=f"carrying relics={len(carried_relics)} packs={len(carried_packs)}; bias toward survival",
                    source_facts=["F|progression.settlement", "F|progression.inventory"],
                )
            )

        return results


def _best_cargo_escape_region(regions: list[dict]) -> str:
    scored: list[tuple[float, str]] = []
    for region in regions:
        if not isinstance(region, dict):
            continue
        region_id = str(region.get("id") or region.get("regionId") or "")
        if not region_id:
            continue
        label = f"{region.get('name', '')} {region.get('terrain', '')}".lower()
        score = 0.0
        if "medical" in label or "heal" in label:
            score += 18
        if "ruin" in label:
            score -= 10
        if "storm" in label or "water" in label:
            score -= 8
        score += min(8, len(region.get("connections", []) or []))
        scored.append((score, region_id))
    return sorted(scored, key=lambda item: (item[0], item[1]), reverse=True)[0][1] if scored else ""
