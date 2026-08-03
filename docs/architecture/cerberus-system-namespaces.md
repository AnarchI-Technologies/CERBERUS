# CERBERUS system namespaces

Status: canonical v1

The machine-readable authority for these names is
[`cerberus-system.v1.json`](cerberus-system.v1.json).

## System boundary

**CERBERUS** names the complete system. It is the boundary around every user
surface, account runtime, memory facility, lifecycle process, signal route, and
extension boundary. No individual executable, server process, or library may be
named CERBERUS as though it alone were the system.

## Named responsibilities

| Name | Responsibility |
|---|---|
| **Helm** | Windows dashboard for interaction, local operation, configuration, and administration. |
| **Vigil** | Android surface for observation, alerts, and memory lookup. It is read-only unless granted Remote Reach. |
| **Spine** | Account-scoped sessions, permissions, state, and orchestration. |
| **Neural Network** | Memory, context, and learning. It does not own lifecycle or routing. |
| **Pulse** | Health, lifecycle, and readiness. |
| **Switch Board** | Typed signal publication, subscription, and routing. |
| **Co'neck'tion** | Lineage-neutral normalization, translation, validation, and correlation. |

## Named concepts

**Reach** is a permission scope, not an application. Read-only, Standard,
Remote, Elevated, and Time-limited Reach describe how far a session may act.
Windows operating-system elevation and Elevated Reach are independent gates.

A **Neck** is one translation boundary between a native lineage and the common
CERBERUS language. Games, chains, protocols, tools, and local processes can be
implemented by unrelated technologies while remaining intelligible through
Co'neck'tion.

## Responsibility order

Pulse proves a participant is alive. Co'neck'tion makes its native language
understood. Switch Board makes its normalized signals heard. Neural Network
makes retained experience meaningful. Spine applies account scope and
permissions. Helm and Vigil make the system usable.

Incoming signals follow:

```text
native source -> Neck -> Co'neck'tion -> Switch Board -> Spine -> Neural Network or surface
```

Outgoing commands follow:

```text
surface or CERBERUS intent -> Spine -> Switch Board -> Co'neck'tion -> Neck -> native destination
```

## Technical spelling

The apostrophes in **Co'neck'tion** are presentation only. Code, packages,
paths, URLs, schemas, and environment variables use `conecktion`. Canonical
identifiers use lowercase ASCII kebab-case. Product names remain between one
and three words and do not use implementation-style suffixes.

Canonical examples:

```text
cerberus.helm
cerberus.vigil
cerberus.spine
cerberus.conecktion
cerberus.pulse
cerberus.switch-board
```
