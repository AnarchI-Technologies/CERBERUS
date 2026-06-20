"""
Social Cortex: MoltyBook persona, taunts, follows, and social learning.

Public voice rule:
- Clever, whimsical, tactful, playful.
- Share real strategy principles.
- Never share deterministic chains, formulas, secret keys, or exact scoring logic.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from agent_relations import alliance_value, is_allied
from agent_dossiers import AgentDossierStore
from cortex_types import CortexResult, action
from external_wisdom import handoff_policy, truthfulness_policy
from predator_mode import event_looks_outsmarted
from strategy_validation import StrategyValidator
from turn_state_model import AgentState, TurnState


SUBMOLTS = {
    "combat": "submolt/combat-stories",
    "strategy": "submolt/strategy-table",
    "progression": "submolt/ruins-relics-packs",
    "dev": "submolt/game-development",
}
LEAKY_TERMS = re.compile(
    r"deterministic chain|decision chain|exact score|formula|source code|"
    r"private key|api key|seed phrase|mnemonic|wallet secret|raw prompt",
    re.I,
)


@dataclass(slots=True)
class MoltyBookDraft:
    category: str
    content: str
    submolt: str = ""
    target_agent_id: str = ""
    target_handle: str = ""

    def side_effect(self) -> dict[str, Any]:
        return {
            "type": "moltybook_draft",
            "category": self.category,
            "content": self.content[:500],
            "submolt": self.submolt,
            "targetAgentId": self.target_agent_id,
            "targetHandle": self.target_handle,
        }


class PersonaPolicy:
    taunts = [
        "{name}, that was a dazzling shortcut back to the lobby. I left a ribbon on the door for you.",
        "{name}, you made a bold thesis. The arena peer-reviewed it rather quickly.",
        "{name}, beautiful footwork right up until the floor voted otherwise.",
        "{name}, your plan had sparkle. Mine had an exit clause.",
        "{name}, I admire the confidence. The scoreboard appears to prefer my interpretation.",
        "{name}, you almost had me. I simply moved the punchline three tiles earlier.",
    ]
    outsmart = [
        "{name} followed the breadcrumbs and discovered they were confetti. Elegant little lesson.",
        "{name} chose the obvious path. I had already signed it in disappearing ink.",
        "{name} saw the bait, saluted it, and made it official. Splendid theater.",
    ]
    strategy_share = [
        "Real tip: ruins are worth chasing only when your escape route and Alert budget both survive the math.",
        "Useful rule of thumb: a relic you live with is worth more than a perfect relic you drop on the floor.",
        "Public strategy morsel: guardian pressure is not just damage, it is timing. Enter noisy, leave quiet.",
        "Combat note: winning a fight that strands your EP can still be losing the turn after.",
    ]

    def taunt_for(self, name: str, *, outsmarted: bool = False) -> str:
        template = random.choice(self.outsmart if outsmarted else self.taunts)
        return template.format(name=name or "friend")[:200]

    def rival_taunt(self, name: str) -> str:
        lines = [
            "{name}, our little series continues. I kept the receipt this time.",
            "{name}, familiar face, familiar outcome. I do enjoy consistency when it favors me.",
            "{name}, the rematch has been filed under recurring entertainment.",
        ]
        return random.choice(lines).format(name=name or "friend")[:200]

    def respectful_challenge(self, name: str) -> str:
        lines = [
            "{name}, respect. You keep earning the last word, so I will bring a better opening line next time.",
            "{name}, clean work. I owe you a sharper rematch and I intend to pay in full.",
            "{name}, kudos. You have my attention now, and probably my next challenge post too.",
        ]
        return random.choice(lines).format(name=name or "friend")[:220]

    def public_strategy(self) -> str:
        return random.choice(self.strategy_share)

    def sanitize_public(self, content: str) -> str:
        cleaned = LEAKY_TERMS.sub("[kept private]", content)
        return " ".join(cleaned.split())[:500]


class SocialCortex:
    name = "social"

    def __init__(
        self,
        *,
        dossier_store: AgentDossierStore | None = None,
        persona: PersonaPolicy | None = None,
        validator: StrategyValidator | None = None,
    ):
        self.dossiers = dossier_store or AgentDossierStore().load()
        self.persona = persona or PersonaPolicy()
        self.validator = validator or StrategyValidator()

    def evaluate(self, state: TurnState, context: dict) -> list[CortexResult]:
        results: list[CortexResult] = []
        side_effects: list[dict[str, Any]] = []

        for agent in state.visible_agents:
            if agent.id and agent.id != state.self.id:
                self._observe_agent(agent)
                side_effects.extend(self._follow_effects(agent))

        for event in state.events:
            side_effects.extend(self._event_effects(state, event))

        for message in state.recent_messages:
            side_effects.extend(self._message_effects(state, message))

        if side_effects:
            # Adjust priority: social interactions are less important if we are dying
            base_priority = 30
            if state.self.hp and state.self.hp < 25:
                base_priority = 5

            results.append(
                CortexResult(
                    cortex=self.name,
                    intent="social_side_effects",
                    score=18,
                    risk=2,
                    priority=base_priority,
                    action=None,
                    reason="MoltyBook/follow/dossier side effects prepared",
                    side_effects=side_effects,
                    source_facts=["F|action.free", "F|memory.policy"],
                )
            )
        return results

    def _observe_agent(self, agent: AgentState) -> None:
        tendency = ""
        if agent.hp and agent.hp <= 35:
            tendency = "visible_low_hp"
        elif agent.ep and agent.ep <= 2:
            tendency = "visible_low_ep"
        self.dossiers.observe_agent_profile(
            agent.id,
            name=agent.name,
            tendency=tendency,
            handle=self._agent_handle(agent),
        )

    def _follow_effects(self, agent: AgentState) -> list[dict[str, Any]]:
        handle = self._agent_handle(agent) or self.dossiers.social_handle_for(agent.id)
        if not handle:
            return []
        record = self.dossiers.record_social_profile(agent.id, handle=str(handle))
        if record.followed:
            return []
        record.followed = True
        return [{
            "type": "moltybook_follow",
            "agentId": agent.id,
            "handle": str(handle),
            "reason": "encountered on battlefield",
        }]

    def _event_effects(self, state: TurnState, event: dict[str, Any]) -> list[dict[str, Any]]:
        event_type = str(event.get("type") or event.get("eventType") or "").lower()
        data = event.get("data") if isinstance(event.get("data"), dict) else event
        killed = any(term in event_type for term in ("kill", "death", "eliminated"))
        if not killed:
            return []

        killer = str(data.get("killerId") or data.get("attackerId") or "")
        victim = str(data.get("victimId") or data.get("agentId") or data.get("targetId") or "")
        if killer == state.self.id and victim and victim != state.self.id:
            victim_name = str(data.get("victimName") or data.get("targetName") or victim[:8])
            outsmarted = event_looks_outsmarted(event)
            record = self.dossiers.observe_agent(victim, name=victim_name)
            if is_allied(record):
                self.dossiers.record_betrayal_by_us(
                    victim,
                    name=victim_name,
                    note=f"silent_betrayal@{state.current_region.name or state.current_region.id or 'arena'}",
                )
                return [{
                    "type": "silent_betrayal_recorded",
                    "agentId": victim,
                    "alliance_value": alliance_value(record),
                    "reason": "allied target was eliminated without public taunt",
                }]
            handle = self._tag_handle(record.moltybook_handle)
            base = (
                self.persona.rival_taunt(record.name or victim_name)
                if int(record.killed_by_us or 0) >= 1
                else self.persona.taunt_for(record.name or victim_name, outsmarted=outsmarted)
            )
            content = f"{handle} {base}".strip()
            draft = MoltyBookDraft(
                category="kill_taunt" if not outsmarted else "outsmart_taunt",
                content=self.persona.sanitize_public(content + " #ClawRoyale #Cerberus"),
                submolt=SUBMOLTS["combat"],
                target_agent_id=victim,
                target_handle=record.moltybook_handle,
            )
            effects = [draft.side_effect()]
            if state.has_broadcast_channel:
                effects.append({
                    "type": "game_free_action",
                    "action": action("broadcast", message=content[:200]),
                    "reason": "playful post-kill taunt",
                })
            return effects

        if victim == state.self.id and killer and killer != state.self.id:
            killer_name = str(data.get("killerName") or data.get("attackerName") or killer[:8])
            record = self.dossiers.observe_agent(killer, name=killer_name)
            if alliance_value(record) > 0:
                self.dossiers.record_betrayal_by_them(
                    killer,
                    name=killer_name,
                    note=f"alliance_broken@{state.current_region.name or state.current_region.id or 'arena'}",
                )
            if int(record.killed_us or 0) < 1:
                return []
            handle = self._tag_handle(record.moltybook_handle)
            content = f"{handle} {self.persona.respectful_challenge(record.name or killer_name)}".strip()
            return [
                MoltyBookDraft(
                    category="respectful_challenge",
                    content=self.persona.sanitize_public(content + " #ClawRoyale #Cerberus"),
                    submolt=SUBMOLTS["combat"],
                    target_agent_id=killer,
                    target_handle=record.moltybook_handle,
                ).side_effect()
            ]
        return []

    def _message_effects(self, state: TurnState, message: dict[str, Any]) -> list[dict[str, Any]]:
        text = str(message.get("message") or message.get("content") or "")
        author = str(message.get("agentId") or message.get("authorId") or "")
        if not text:
            return []

        effects: list[dict[str, Any]] = []
        lowered = text.lower()
        helpful_terms = ("strategy", "strat", "ruin", "relic", "guardian", "alert", "exit", "loot", "watch", "careful")
        alliance_terms = ("truce", "ally", "alliance", "friend", "together", "team up", "don't fight", "dont fight")
        truth_policy = truthfulness_policy()
        handoff = handoff_policy()
        uncertainty_markers = truth_policy.get("preferred_markers", ["maybe", "likely", "watch", "careful", "if", "probably"])
        truthful = bool(truth_policy.get("reward_uncertainty_markers")) and any(marker in lowered for marker in uncertainty_markers if isinstance(marker, str))
        alliance_offer = any(term in lowered for term in alliance_terms)
        if any(term in lowered for term in helpful_terms):
            validation = self.validator.validate(text)
            if validation.accepted and author:
                marker = self.dossiers.add_validated_strategy(author, validation.compact_note)
                self.dossiers.record_helpful_message(
                    author,
                    note=f"handoff:{marker[:40]}",
                    truthful=truthful,
                    alliance_offer=alliance_offer,
                )
                effects.append({
                    "type": "validated_strategy_soundbite",
                    "agentId": author,
                    "marker": marker,
                    "confidence": round(validation.confidence, 3),
                    "reason": validation.reason,
                })
                if handoff.get("structured_handoff_preferred"):
                    effects.append({
                        "type": "relationship_handoff",
                        "agentId": author,
                        "uncertainty": truthful,
                        "alliance_offer": alliance_offer,
                        "required_fields": handoff.get("required_fields", []),
                    })

        elif author and alliance_offer:
            self.dossiers.record_helpful_message(
                author,
                note="handoff:truce_offer",
                truthful=truthful,
                alliance_offer=True,
            )

        if author and alliance_offer:
            reply = "Truce accepted while the math favors it. Cross me and the lesson updates immediately."
            effects.append({
                "type": "game_free_action",
                "action": action("whisper", targetId=author, message=reply[:200]),
                "reason": "acknowledge provisional alliance without promising forever",
            })

        if author and "how" in lowered and "win" in lowered:
            reply = self.persona.public_strategy()
            effects.append({
                "type": "game_free_action",
                "action": action("whisper", targetId=author, message=reply[:200]),
                "reason": "share bounded strategy with another agent",
            })
        return effects

    @staticmethod
    def _agent_handle(agent: AgentState) -> str:
        if not isinstance(getattr(agent, "raw", None), dict):
            return ""
        return str(
            agent.raw.get("moltybookHandle")
            or agent.raw.get("moltbookHandle")
            or agent.raw.get("socialHandle")
            or ""
        ).strip()

    @staticmethod
    def _tag_handle(handle: str) -> str:
        text = str(handle or "").strip().lstrip("@")
        return f"@{text}" if text else ""
