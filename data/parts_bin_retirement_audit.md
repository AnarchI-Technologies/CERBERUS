# parts.bin Retirement Audit

Date: 2026-06-15

## Decision

`parts.bin/mmmmm-main` was an old full bot implementation plus RSS documentation snapshots. It is not imported by the active CERBERUS runtime and is ignored from Git. Keeping it in the repo root increases confusion because it preserves old Railway, MongoDB, REST join, and optional OpenAI paths beside the current Render and deterministic cortex runtime.

The folder is safe to retire only after its remaining useful rules are represented in current source or preserved as compact audit knowledge.

## Retained In Current Runtime

- Runtime join truth: current CERBERUS uses `/ws/join`, live version reconciliation, API key headers, and paid signing in `src/claw_runtime.py` and `src/claw_signing.py`.
- Wallet and identity bootstrapping: current CERBERUS uses the identity vault, purpose map, ERC-8004 identity registration, and Render env export modules.
- Weapon and combat handling: current CERBERUS has deterministic weapon bonuses, range checks, guardian target scoring, low-damage attack rejection, and legalizers.
- Free-action posture: pickup/equip/talk/whisper/broadcast are represented in current contract and cortex code.
- Memory: current CERBERUS uses compact memory plus SQLite long-term memory instead of the old Mongo/Railway variable model.
- Dashboard: current CERBERUS has Render `/dashboard` and `/stream`; old static dashboard is superseded.

## Newly Extracted Before Retirement

- Settlement outcome lessons from the old `bot/game/settlement.py` are now represented by `src/settlement_memory.py`.
- Utility item behavior from the old brain is now represented by `src/utility_cortex.py`.
- Fallback movement scoring now carries forward terrain, loot, and pursuit pressure from the old brain without importing the monolith.
- Missing knowledge compactor sources are restored as `data/cerberus_mmmmm_knowledge_digest.md` and `data/legacy_brain_analysis.md`.

## Intentionally Discarded

- Railway setup, Railway variable sync, Railway deployment files, and Railway dashboard instructions.
- MongoDB memory dependency.
- Optional OpenAI post-game analysis path; CERBERUS remains deterministic-first.
- Legacy REST paid join path through `/games/{id}/join-paid`; current docs and runtime prefer WebSocket paid join with `sign_required` and `sign_submit`.
- Old hardcoded strategy globals from `bot/strategy/brain.py`; useful behavior was decomposed into deterministic cortex modules instead.
- Old static dashboard assets; Render dashboard and stream views supersede them.

## Retirement Rule

After archiving the folder to `C:\AnarchI-IP`, `parts.bin` may be removed from the CERBERUS working tree. If a future question needs the original source, use the archive instead of reintroducing the folder into the runtime repo.
