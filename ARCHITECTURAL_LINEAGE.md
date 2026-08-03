# CERBERUS Architectural Lineage

## Repository status

This repository is the canonical historical monorepo from which the
CERBERUS platform repositories were extracted.

It remains authoritative for:

- original implementation history
- architectural evolution
- test provenance
- migration evidence
- historical decisions
- source commit references

## Descendant repositories

| Capability | Descendant repository | Source paths | Extraction commit |
|---|---|---|---|
| Runtime lifecycle | pulse-runtime | src/pulse.py | pending |
| Event bus | switch-board | src/switch_board | pending |
| Memory | anar-neural-network | data/memory_system.py | pending |
| Claw integration | claw-royale-adapter | src/claw_royale | pending |
| Strategy | hellion-strategist | src/claw_royale/ai | pending |

## Migration rule

No capability is considered migrated until:

1. Its descendant repository builds independently.
2. Its inherited tests pass independently.
3. Contract tests pass against CERBERUS.
4. Provenance is recorded.
5. CERBERUS delegates to the extracted package.
6. The full CERBERUS suite remains green.
