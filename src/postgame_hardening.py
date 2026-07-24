"""Post-match maintenance: rescan memory, harden strategy, and hydrate social."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from agent_dossiers import AgentDossierStore
from hardened_strategy import (
    hardened_strategy_rules_file,
    load_hardened_strategy_rules,
    sanity_check_rules,
    save_hardened_strategy_rules,
)
from external_wisdom import (
    memory_hierarchy_policy,
    posting_prerequisites_for,
    scheduler_policy,
    social_tone_for,
    social_policy,
    tagging_rules,
    validated_strategy_wisdom,
    voice_guardrails,
    voice_soundbites,
)
from lesson_compiler import compile_lessons
from memory_system import CompactMemoryStore, parse_compact_segments, scrub_scalar, stable_hash, utc_now
from runtime_state import (
    approved_suggested_edits,
    hellion_voice_lab,
    social_event_stack,
    update_hellion_voice_lab,
    update_social_event_status,
    update_suggested_edit_status,
)
from social_cortex import MoltyBookDraft, PersonaPolicy, SUBMOLTS
from social_runtime import drain_social_queue_once, enqueue_social_effects


def _lesson_text(line: str) -> str:
    if not isinstance(line, str):
        return ""
    return str(parse_compact_segments(line).get("L", {}).get("txt", ""))


def _compile_memory_snapshot(memory: CompactMemoryStore) -> list[str]:
    lessons = memory.data.get("lessons", [])
    if not isinstance(lessons, list):
        return []
    return [str(item) for item in lessons if isinstance(item, str)]


def _candidate_rules(
    *,
    compiled: dict[str, Any],
    memory: CompactMemoryStore,
    dossiers: AgentDossierStore,
    approved: list[dict[str, Any]],
    prior_payload: dict[str, Any],
) -> dict[str, Any]:
    candidate = dict(prior_payload)
    candidate["updated_at"] = utc_now()
    rules = dict(prior_payload.get("rules") or {})
    rules.setdefault("heal_hp_ratio_floor", 0.68)
    rules.setdefault("known_killer_hp_ratio_floor", 0.72)
    rules.setdefault("observed_finisher_hp_ratio_floor", 0.70)
    rules.setdefault("repeat_prey_min_kills", 1)
    rules["postgame_memory_rescan_before_loadout"] = True
    rules["social_autodrain_on_enqueue"] = True
    rules["cross_agent_dossier_learning"] = True
    rules["prefer_failure_memory_weighting"] = True
    rules["consistent_public_persona"] = True
    rules["bounded_public_persona"] = True
    rules["external_wisdom_validation_required"] = True
    rules["heartbeat_lightweight_only"] = True
    rules["postgame_batch_window"] = True
    rules["social_post_requires_trigger"] = True
    rules["social_focus_submolts"] = True
    rules["layered_memory_hierarchy"] = True

    lesson_rows = _compile_memory_snapshot(memory)
    death_lessons = sum(1 for line in lesson_rows if "failure:" in line.lower() or "eliminated us" in line.lower())
    repeat_killers = sum(1 for record in dossiers.records.values() if int(record.killed_us or 0) >= 2)
    imported_wisdom = validated_strategy_wisdom()
    imported_keys = {str(item.get("key") or "") for item in imported_wisdom}
    cadence = scheduler_policy()
    if death_lessons >= 2 or repeat_killers:
        rules["heal_hp_ratio_floor"] = max(float(rules["heal_hp_ratio_floor"]), 0.72)
        rules["known_killer_hp_ratio_floor"] = max(float(rules["known_killer_hp_ratio_floor"]), 0.78)
        rules["observed_finisher_hp_ratio_floor"] = max(float(rules["observed_finisher_hp_ratio_floor"]), 0.74)
    if "failure_registry_over_victory_laps" in imported_keys and repeat_killers:
        rules["known_killer_hp_ratio_floor"] = max(float(rules["known_killer_hp_ratio_floor"]), 0.80)
    if "bounded_public_persona" in imported_keys:
        rules["consistent_public_persona"] = True
        rules["bounded_public_persona"] = True
    if "validation_gate_multi_agent_wisdom" in imported_keys:
        rules["external_wisdom_validation_required"] = True
    if "heartbeat_vs_cron_split" in imported_keys:
        rules["heartbeat_lightweight_only"] = bool(cadence.get("heartbeat_lightweight_only", True))
        rules["postgame_batch_window"] = bool(cadence.get("postgame_batch_window", True))
    if "submolt_focus_and_triggered_posting" in imported_keys:
        rules["social_post_requires_trigger"] = True
        rules["social_focus_submolts"] = True
    if "memory_hierarchy_over_junk_drawer" in imported_keys:
        rules["layered_memory_hierarchy"] = True

    approved_detectors = {str(item.get("detector") or "") for item in approved}
    if "runtime.target_blocked" in approved_detectors:
        rules["repeat_prey_min_kills"] = max(int(rules["repeat_prey_min_kills"]), 1)
    if "contract.insufficient_ep" in approved_detectors or "runtime.cooldown_waste" in approved_detectors:
        rules["heal_hp_ratio_floor"] = max(float(rules["heal_hp_ratio_floor"]), 0.70)

    hardened_from = {
        "lessons": [str(item.get("key") or item.get("text") or "") for item in compiled.get("lessons", [])[:24]],
        "suggested_edits": [str(item.get("id") or "") for item in approved],
        "external_wisdom": sorted(imported_keys),
        "source_hash": str(compiled.get("source_hash") or stable_hash([lesson_rows, approved_detectors], length=24)),
    }
    candidate["rules"] = rules
    candidate["hardened_from"] = hardened_from
    return candidate


def _apply_memory_hierarchy(memory: CompactMemoryStore) -> dict[str, Any]:
    policy = memory_hierarchy_policy()
    if not policy:
        return {}
    for attr in ("max_short_turns", "max_summaries", "max_lessons", "max_facts", "context_bytes"):
        value = policy.get(attr)
        if isinstance(value, int) and value > 0:
            setattr(memory, attr, value)
    memory.set_profile(
        memory_profile=str(policy.get("profile_name") or "layered_compact_memory"),
        dossier_never_redact=bool(policy.get("dossier_never_redact", True)),
        social_stack_separate=bool(policy.get("social_stack_separate", True)),
        memory_hierarchy="turns->summaries->lessons->dossiers",
        memory_tiers=",".join(str(item) for item in policy.get("tier_order", []) if item),
        lesson_redaction_after_hardening=bool(policy.get("lesson_redaction_after_hardening", True)),
    )
    return policy


def _sanitize_handle(handle: str) -> str:
    text = scrub_scalar(handle, limit=40).lstrip("@")
    return f"@{text}" if text else ""


def _social_drafts_from_stack(dossiers: AgentDossierStore) -> tuple[list[dict[str, Any]], list[str]]:
    persona = PersonaPolicy()
    drafts: list[dict[str, Any]] = []
    consumed: list[str] = []
    for event in social_event_stack(limit=80, status="queued"):
        event_id = str(event.get("id") or "")
        kind = str(event.get("kind") or "")
        agent_id = str(event.get("agent_id") or "")
        record = dossiers.records.get(agent_id) if agent_id else None
        name = str(event.get("agent_name") or getattr(record, "name", "") or agent_id[:8] or "rival")
        handle = _sanitize_handle(str(event.get("moltybook_handle") or getattr(record, "moltybook_handle", "")))
        prefix = f"{handle} " if handle else ""
        if kind == "player_kill":
            rivalry = " We have history now." if record and int(getattr(record, "killed_by_us", 0) or 0) >= 2 else ""
            outsmarted = str(event.get("detail") or "").lower().find("outsmart") >= 0
            content = prefix + persona.taunt_for(name, outsmarted=outsmarted) + rivalry + " #ClawRoyale #Cerberus"
            drafts.append(
                MoltyBookDraft(
                    category="kill_taunt",
                    content=persona.sanitize_public(content),
                    submolt=SUBMOLTS["combat"],
                    target_agent_id=agent_id,
                    target_handle=handle,
                ).side_effect()
            )
        elif kind == "killed_by_rival" and record and int(getattr(record, "killed_us", 0) or 0) >= 2:
            content = (
                f"{prefix}{name}, respect where it is due. You keep finding the winning line against me. "
                "Friendly feud accepted. Next ring, same fire. #ClawRoyale #Cerberus"
            )
            drafts.append(
                MoltyBookDraft(
                    category="respectful_challenge",
                    content=persona.sanitize_public(content),
                    submolt=SUBMOLTS["combat"],
                    target_agent_id=agent_id,
                    target_handle=handle,
                ).side_effect()
            )
        elif kind == "match_end" and str(event.get("detail") or ""):
            content = f"Hellion ledger update: {scrub_scalar(event.get('detail'), limit=220)} #ClawRoyale #Cerberus"
            drafts.append(
                MoltyBookDraft(
                    category="match_summary",
                    content=persona.sanitize_public(content),
                    submolt=SUBMOLTS["progression"],
                ).side_effect()
            )
        if drafts and event_id:
            consumed.append(event_id)
    return drafts, consumed


def _apply_rules_after_sanity(payload: dict[str, Any], *, rules_path: str | Path | None = None) -> dict[str, Any]:
    rules_path = Path(rules_path) if rules_path is not None else hardened_strategy_rules_file()
    check = sanity_check_rules(payload)
    if not check.get("ok"):
        return check
    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp) / "hardened_strategy_rules.json"
        save_hardened_strategy_rules(payload, probe)
        reloaded = load_hardened_strategy_rules(probe)
        recheck = sanity_check_rules(reloaded)
        if not recheck.get("ok"):
            return {"ok": False, "error": "reload_sanity_failed", "detail": recheck}
    save_hardened_strategy_rules(payload, rules_path)
    return {"ok": True, "path": str(Path(rules_path))}


def _redact_hardened_lessons(memory: CompactMemoryStore, compiled: dict[str, Any]) -> int:
    compiled_rows = [
        item for item in compiled.get("lessons", [])
        if isinstance(item, dict)
    ]
    compiled_texts = {str(item.get("text") or "") for item in compiled_rows if item.get("text")}
    compiled_keys = {str(item.get("key") or "") for item in compiled_rows if item.get("key")}
    if not compiled_rows and not compiled_texts and not compiled_keys:
        return 0
    lessons = memory.data.get("lessons", [])
    if not isinstance(lessons, list):
        return 0
    kept: list[str] = []
    for line in lessons:
        parsed = parse_compact_segments(line).get("L", {}) if isinstance(line, str) else {}
        source = str(parsed.get("src", "") or "")
        text = str(parsed.get("txt", "") or "")
        should_redact = False
        if source == "lesson_compiler":
            should_redact = True
        elif text and text in compiled_texts:
            should_redact = True
        elif compiled_keys and any(key and key in text for key in compiled_keys):
            should_redact = True
        if not should_redact:
            kept.append(line)
    removed = len(lessons) - len(kept)
    memory.data["lessons"] = kept
    return max(0, removed)


def _autodrain_social_if_enabled() -> dict[str, Any]:
    enabled = os.getenv("CERBERUS_MOLTYBOOK_ENABLED", "false").strip().lower() == "true"
    has_key = bool(os.getenv("MOLTYBOOK_API_KEY") or os.getenv("MOLTBOOK_API_KEY"))
    if not enabled or not has_key:
        return {"ok": True, "processed": 0, "reason": "moltybook disabled_or_missing_key"}
    return drain_social_queue_once(max_items=5)


def _hydrate_voice_from_wisdom() -> dict[str, Any]:
    current = hellion_voice_lab()
    soundbites = current.get("soundbites", []) if isinstance(current.get("soundbites"), list) else []
    guardrails = current.get("guardrails", []) if isinstance(current.get("guardrails"), list) else []
    for line in voice_soundbites():
        payload = {"text": scrub_scalar(line, limit=180), "mood": "sharp", "source": "external_wisdom"}
        if payload not in soundbites:
            soundbites.append(payload)
    for line in voice_guardrails():
        clean = scrub_scalar(line, limit=180)
        if clean and clean not in guardrails:
            guardrails.append(clean)
    return update_hellion_voice_lab(
        source="external wisdom library",
        soundbites=soundbites[-12:],
        guardrails=guardrails[-12:],
    )


def run_postgame_hardening_pass(
    *,
    memory: CompactMemoryStore | None = None,
    dossiers: AgentDossierStore | None = None,
    rules_path: str | Path | None = None,
) -> dict[str, Any]:
    rules_path = Path(rules_path) if rules_path is not None else hardened_strategy_rules_file()
    memory_store = memory or CompactMemoryStore().load()
    dossier_store = dossiers or AgentDossierStore().load()
    memory_policy = _apply_memory_hierarchy(memory_store)
    compiled = compile_lessons(memory=memory_store, dossiers=dossier_store, min_count=2)
    approved = approved_suggested_edits(limit=80)
    prior = load_hardened_strategy_rules(rules_path)
    candidate = _candidate_rules(
        compiled=compiled,
        memory=memory_store,
        dossiers=dossier_store,
        approved=approved,
        prior_payload=prior,
    )
    apply_result = _apply_rules_after_sanity(candidate, rules_path=rules_path)
    redacted_lessons = 0
    hardened_edits: list[str] = []
    if apply_result.get("ok"):
        redacted_lessons = _redact_hardened_lessons(memory_store, compiled)
        for edit in approved:
            edit_id = str(edit.get("id") or "")
            if edit_id:
                update_suggested_edit_status(edit_id, "hardened", note="promoted during postgame sanity hardening")
                hardened_edits.append(edit_id)
        memory_store.save()
    drafts, social_event_ids = _social_drafts_from_stack(dossier_store)
    queued = enqueue_social_effects(drafts) if drafts else []
    for event_id in social_event_ids:
        update_social_event_status(event_id, "drafted", note="converted to social queue during postgame maintenance")
    social_drain = _autodrain_social_if_enabled()
    voice_lab = _hydrate_voice_from_wisdom()
    return {
        "ok": bool(apply_result.get("ok")),
        "compiled": compiled,
        "approved_suggestions_seen": len(approved),
        "approved_suggestions_hardened": len(hardened_edits),
        "redacted_lessons": redacted_lessons,
        "rules_path": str(rules_path),
        "rules": candidate.get("rules", {}),
        "social_drafts": len(drafts),
        "social_queue_depth": len(queued),
        "social_drain": social_drain,
        "voice_lab_soundbites": len(voice_lab.get("soundbites", [])) if isinstance(voice_lab, dict) else 0,
        "memory_policy": memory_policy,
        "social_policy": social_policy(),
        "scheduler_policy": scheduler_policy(),
        "sample_social_tones": {
            "kill_taunt": social_tone_for("kill_taunt"),
            "match_summary": social_tone_for("match_summary"),
        },
        "sample_social_prerequisites": {
            "kill_taunt": posting_prerequisites_for("kill_taunt"),
            "match_summary": posting_prerequisites_for("match_summary"),
        },
        "tagging_rules": tagging_rules(),
        "applied_at": utc_now(),
        "error": "" if apply_result.get("ok") else str(apply_result.get("error") or "hardening_failed"),
    }
