# Preseason 1 level-five sprint

The July 18 objective snapshot shows the largest deficit in **Off the Beaten Path** (level 0), followed by rival kills, damage, top-five finishes, survival, paid entries, and Moltz earnings. Collector, Forgemaster, and Daily Devotion are already at or above level 5.

The deterministic sprint policy therefore:

- prioritizes uncontested exploration while retaining two EP;
- continues a known relic ruin only with fewer than five carried relics, adequate HP, no active combat, and an EP reserve;
- takes only one-hit rival opportunities that retain at least 65% projected HP and one EP;
- preserves top-ten survival above exploration;
- keeps weapon upgrades, danger escape, healing, and direct relic pickups above exploration;
- never weakens paid-entry readiness, wallet, signing, or bankroll safeguards.

Official game knowledge can be refreshed with:

```powershell
.\.venv\Scripts\python.exe src\claw_knowledge_sync.py
```

The sync uses unauthenticated GET requests against official `clawroyale.ai` sources, rejects redirects outside that domain, records source hashes, and writes `data/claw_royale_canonical_snapshot.md`. Generated text is review evidence only and cannot change live gameplay policy by itself.

The local operator node also runs `cerberus-claw-knowledge-sync.timer` every six
hours. Its runtime copy is stored outside Git at
`/var/data/.cerberus/claw_royale_canonical_snapshot.md`; only a reviewed manual
sync is promoted into the tracked `data/` snapshot.
