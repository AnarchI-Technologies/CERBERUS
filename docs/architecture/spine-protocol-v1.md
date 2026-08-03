# Spine protocol v1

Spine is the account-scoped client boundary for Helm and Vigil. Version 1 is
read-first: it proves authentication, surface identity, Reach, and a bounded
snapshot before any remote mutation protocol is introduced.

## Endpoints

| Method | Path | Reach | Purpose |
|---|---|---|---|
| `GET` | `/spine/v1/session` | Read-only | Return the authenticated surface, effective Reach, and available capabilities. |
| `GET` | `/spine/v1/snapshot` | Read-only | Return bounded health, runtime, memory counts, activity counts, and launch blockers. |

Both endpoints require the existing CERBERUS bearer boundary. A loopback
request accepted by the local trust policy receives Standard Reach; a remote
bearer session remains Read-only. Headers cannot promote Reach. Elevated Reach
continues to require an independent administrative authorization and is not
part of this protocol version.

Clients may identify themselves with `X-Cerberus-Surface: helm` or
`X-Cerberus-Surface: vigil`. Unknown values are normalized to `unknown` and do
not change authorization.

The snapshot is an explicit projection. It never forwards internal host paths,
environment state, administrative settings, credentials, wallets, or raw
memory records. The canonical schema is
[`schemas/spine.protocol.v1.json`](../../schemas/spine.protocol.v1.json).
