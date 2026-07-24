"""Stable identities for every currently observed Claw Royale strategy."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class StrategyDescriptor:
    strategy_id: str
    intent: str
    provider: str
    ordinal: int
    required_signals: frozenset[str] = frozenset()


def _family(
    provider: str,
    intents: tuple[str, ...],
    *,
    required_signals: frozenset[str] = frozenset(),
) -> tuple[StrategyDescriptor, ...]:
    return tuple(
        StrategyDescriptor(
            strategy_id=f"clawroyale.{intent}",
            intent=intent,
            provider=provider,
            ordinal=index,
            required_signals=required_signals,
        )
        for index, intent in enumerate(intents)
    )


STRATEGY_CATALOG = (
    *_family(
        "threat_engine",
        (
            "escape_death_zone",
            "emergency_death_zone_probe",
            "heal_low_hp",
            "break_guardian_alert_exposure",
            "cool_alert",
            "recover_ep_floor",
        ),
    ),
    *_family(
        "owner_command_cortex",
        (
            "owner_requested_heal",
            "owner_requested_weapon_posture",
            "owner_requested_value_pickup",
            "owner_requested_combat_avoidance",
            "owner_requested_defensive_posture",
            "owner_requested_attack_pressure",
            "owner_requested_progression",
            "owner_requested_rest",
            "owner_requested_scouting",
            "owner_requested_public_voice",
        ),
        required_signals=frozenset({"owner_messages"}),
    ),
    *_family(
        "learned_policy_cortex",
        (
            "apply_retained_survival_lesson",
            "preserve_provisional_alliance",
            "betray_when_reward_outweighs_alliance",
            "apply_known_killer_survival_lesson",
            "apply_known_killer_escape_lesson",
            "apply_observed_finisher_survival_lesson",
            "apply_observed_finisher_escape_lesson",
            "apply_repeat_prey_lesson",
            "apply_observed_fragile_target_lesson",
            "apply_known_loot_carrier_lesson",
            "apply_blocked_target_weapon_lesson",
            "apply_blocked_target_reposition_lesson",
            "compiled_lessons_available",
        ),
    ),
    *_family(
        "free_action_abuse",
        (
            "equip_best_weapon",
            "pickup_weapon_upgrade",
            "equip_best_armor",
            "pickup_armor_upgrade",
        ),
        required_signals=frozenset({"free_action_inputs"}),
    ),
    *_family(
        "utility_cortex",
        ("restore_ep_with_energy_item", "use_vision_utility"),
        required_signals=frozenset({"inventory"}),
    ),
    *_family(
        "progression_cortex",
        (
            "explore_ruin",
            "preserve_cargo_heal",
            "preserve_cargo_reposition",
            "preserve_cargo_recover_ep",
            "preserve_progression_cargo",
        ),
    ),
    *_family(
        "quest_rush_cortex",
        (
            "quest_item_pickup",
            "quest_relic_acquisition",
            "quest_discover_ruin",
            "quest_guardian_kill",
            "quest_safe_rival_hunt",
            "quest_top10_reserve",
            "quest_exploration_rotation",
        ),
    ),
    *_family(
        "combat_decider",
        ("take_favorable_attack",),
        required_signals=frozenset({"visible_targets"}),
    ),
    *_family(
        "ep_economy_engine",
        (
            "prime_directive_loadout_pickup",
            "prime_directive_moltz_pickup",
            "hunt_moltz_carrier",
            "collect_free_value",
        ),
        required_signals=frozenset({"economy_inputs"}),
    ),
    *_family(
        "social_cortex",
        ("social_side_effects",),
        required_signals=frozenset({"social_inputs"}),
    ),
    *_family("memory_cortex", ("update_compact_memory",)),
)

STRATEGY_BY_ID = MappingProxyType(
    {descriptor.strategy_id: descriptor for descriptor in STRATEGY_CATALOG}
)

if len(STRATEGY_BY_ID) != len(STRATEGY_CATALOG):
    raise RuntimeError("strategy catalog contains duplicate stable IDs")
