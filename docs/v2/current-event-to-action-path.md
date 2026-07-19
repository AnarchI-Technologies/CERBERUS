# Current Claw Royale event-to-action path

This document describes the pre-v2 boundary so later extraction can preserve
behavior.

1. `src/claw_runtime.py` receives provider WebSocket frames and identifies frame
   type, game state, and terminal conditions.
2. Snapshot frames are parsed by `data/turn_state_model.py` into `TurnState`.
3. `src/core_loop.py` assembles cortex results, memory, dossiers, and knowledge.
4. `src/decision_engine.py` deterministically selects the highest-ranked legal
   candidate and returns an action dictionary.
5. `src/core_loop.py` normalizes and legalizes the action.
6. `src/claw_runtime.py` creates the provider envelope and sends it directly on
   the WebSocket.
7. Action-result frames are compacted into runtime memory and autonomy evidence.
8. Terminal signals trigger balance recording and postgame hardening.

The principal v2 gap is between steps 5 and 6: there is no provider-neutral
action-request contract followed by a central policy decision and isolated
execution adapter. Phase 1 should insert that seam for one read-only or free
Claw Royale action before any paid or signing path is migrated.

