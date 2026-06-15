# Legacy Brain Analysis

This file summarizes the parts-bin `bot/strategy/brain.py` behaviors that were worth keeping after decomposing the old monolithic bot into current CERBERUS cortex modules.

## Useful Behaviors Carried Forward

- Weapon upgrade before combat: current `FreeActionCortex` handles inventory equip and ground pickup upgrades.
- Out-of-range attack rejection: current `CombatCortex` and `legalize_action` reject illegal attacks.
- Guardian value with risk gates: current combat scoring values guardians but avoids low-damage or suicidal fights.
- Death-zone escape: current `ThreatCortex` handles current and pending death-zone pressure.
- Free value pickup: current `EconomyCortex` prioritizes sMoltz, MOLTZ, relics, packs, and recovery items.
- Utility item logic: current `UtilityCortex` handles energy, map, and vision items deterministically.
- Settlement lessons: current `settlement_memory.py` extracts compact post-game lessons.
- Movement scoring: current fallback movement scores terrain, connected value, and safe pursuit pressure.

## Rejected Behaviors

- Global mutable strategy state from the legacy brain is not reused directly.
- LLM-driven post-game analysis is not part of the deterministic default path.
- Old Railway and Mongo persistence assumptions are not retained.
- Old REST paid-join sequence is not used as a canonical join method.
- Old reward numbers are treated as legacy hints unless confirmed in current v1.9 docs.

## Current Architecture Mapping

- Legacy priority chain became modular cortex arbitration.
- Legacy memory lessons became compact memory and SQLite long-term memory.
- Legacy dashboard stats became Render `/stats`, `/dashboard`, and `/stream`.
- Legacy social content became sanitized Social Cortex side effects and public thoughts.
