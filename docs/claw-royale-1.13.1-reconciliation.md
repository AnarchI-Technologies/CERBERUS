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
