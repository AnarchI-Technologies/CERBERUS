# Claw Royale 1.13.1 reconciliation

Authoritative input: the official Claw Royale server snapshot fetched on
2026-07-18 from `cdn.clawroyale.ai/api/version` and `clawroyale.ai/skill.md`.

| Official change | CERBERUS handling |
| --- | --- |
| Same-agent connection ownership (`4030`, `4031`, `4008`) | `4030` holds the same route for at least 60 seconds and raises owner attention; all three codes are explicit contract data. |
| Finished paid REST state includes `room.winners` | Terminal processing falls back to the official game-state route when the event was missed and stores only sanitized top-five display fields. |
| Binoculars reveal stealthed assassins in vision | Contract records the passive and preserves cave concealment. Server-filtered perception remains authoritative. |
| Assassin exposure refreshes on every damaging attack | Contract records the continuous exposure rule; no stale surprise-strike-only assumption remains. |
| Sword Master immunity needs an equipped melee weapon | Contract records the range-zero equipment requirement; barehanded immunity is not assumed. |
| Paid `game_ended.winners[]` | Top-five results are sanitized and recorded with an explicit Moltz unit. |
| Vision Wards are fixed installations | Contract marks them non-lootable, non-plunderable, and non-droppable. |
| Paid Moltz rewards differ from sMoltz fees | Contract forbids direct cross-unit subtraction and labels entry, prize, and dashboard units. |
| `WELCOME` now grants 20 effect-reroll stones | Exact once-per-account bundle and official redeem route are recorded; retries reuse the mandatory stable `Idempotency-Key`. |

This reconciliation does not widen paid-entry, signing, wallet, movement, or
combat authority. Dynamic server snapshots and OpenAPI remain authoritative.

## Production evidence — 2026-07-19 UTC

- The official WELCOME redeem route accepted stable key `cerberus-welcome-v1`
  and returned `replayed=false`: Duelist T2, Iron Heart T3, Ruby, Emerald,
  Sapphire, and 20 Effect Reroll Stones were granted.
- Live OpenAPI verification confirmed numeric `int64` pack/relic instance IDs,
  the dedicated `/api/loadout/sub-pack` route, and mandatory redeem idempotency.
- A legacy string-ID request was rejected without mutation; the corrected
  numeric request was accepted.
- An escrowed marketplace relic was rejected by the server and is now filtered
  from both equip and reforge candidates.
- After applying only a non-listed relic operation, the official loadout response
  reports `fullSet=true`; the deterministic planner reports no missing components
  and no remaining loadout operations.
- The stale terminal room remains in `accounts/me.currentGames`; live OpenAPI has
  no leave/forfeit/recovery route and its finished-state read returns 403 for the
  agent key. CERBERUS therefore keeps the room quarantined and limits clean-close
  reconnect attempts to at most once per 60 seconds until official state clears.
