# CERBERUS MMMMM Knowledge Digest

This digest preserves the useful parts-bin RSS knowledge after retiring `parts.bin/mmmmm-main`.

## Source Precedence

- Current live Claw Royale docs and `data/claw_royale_v1_9_truths.md` outrank all old RSS snapshots.
- `rss220426v152` outranks `rss200426v151` when the two conflict.
- Old strategy code is implementation evidence, not canonical game truth.

## Runtime Knowledge

- Use the unified Claw Royale WebSocket join path `/ws/join`.
- Treat `/join/status` and `/games?status=waiting` as inspection only.
- Paid joins are WebSocket-driven: `welcome`, `hello`, `sign_required`, `sign_submit`, queue or transaction frames, then gameplay.
- Gameplay action frames are sent as `{type:"action",data:{type,...},thought:"..."}`.
- `action_result` and `can_act_changed` are cooldown/status frames, not full strategic snapshots.
- One active gameplay session should exist per API key.

## Game Knowledge

- Death zones are critical survival constraints. Escape current and pending death zones before value or combat.
- Weapons matter more than low-damage attacks. Pick up and equip upgrades before chipping high-defense targets.
- Ranged weapons can make adjacent or distance-tagged targets legal if range permits.
- Guardian fights are valuable only when damage and survival thresholds are favorable.
- Free value pickups, especially sMoltz, relics, packs, and high-tier weapons, are priority actions when legal.
- Ruin exploration is progression-positive only when HP, alert, cargo risk, and death-zone pressure are acceptable.
- Settlement and game-end frames should create compact lessons rather than raw log storage.

## Memory Knowledge

- Store compact lessons like zero-kill exits, high-yield games, top-three survival paths, and aggressive striker games.
- Do not store raw websocket snapshots, private state, keys, OAuth payloads, or full logs.
- Long-term memory should stay typed, compact, and agent-readable.

## Strategy Knowledge Preserved

- Prefer high-value pickups before contested combat when the action is free and legal.
- Avoid duplicate weapon pickup when equipped weapon has equal or better attack value.
- Heal before combat when HP is low and recovery items exist.
- Avoid blind movement into known or pending death zones.
- Use utility items such as map, binoculars, and energy only when they solve an immediate navigation, vision, or EP bottleneck.
- Late-game or wounded-target pursuit is useful only through safe connected regions.
