# External effects inventory

This Phase 0 inventory classifies known effect families. It is intentionally
conservative: unknown calls are treated as consequential until reviewed.

| Effect family | Current area | Risk | Initial v2 capability | Required boundary |
| --- | --- | --- | --- | --- |
| Claw state reads | `claw_runtime`, onboarding client | Read | `game.state.read` | Adapter validation and freshness |
| Free-room join | `claw_runtime` | Write | `game.session.join.free` | Policy record and idempotency |
| Paid-room join/signing | `claw_runtime`, signing | Financial | `game.session.join.paid`, `wallet.transaction.sign` | Simulation, allowlists, limits, review |
| Claw gameplay action | `core_loop`, `claw_runtime` | Write | `game.action.execute` | Action request, policy, execution result |
| Wallet creation/export | identity and wallet modules | Identity | `wallet.identity.manage` | Dedicated secret provider and audit |
| Token transfer/contract call | EVM modules | Financial | `wallet.transaction.sign` | Chain/contract/method/value allowlists |
| Moltbook/MoltyBook publish | social modules | Public write | `social.post.publish` | Proposal/publish separation and rate limit |
| X publish/OAuth | `x_oauth`, social modules | Public write/identity | `social.post.publish` | Token isolation, approval policy, audit |
| AgentMail send | onboarding/identity modules | External write | `email.send` | Recipient allowlist and content policy |
| MongoDB/SQLite memory write | memory modules | Data write | `memory.write` | Classification and retention |
| Runtime status files | `runtime_state` | Local state | `runtime.state.write` | Ownership, atomic checkpoint contract |
| Dashboard/admin operations | `render_app` | Administrative | `control.runtime.manage` | Protected API and emergency suspension |

## Immediate containment rules

- No new write-capable integration during Phase 0.
- Paid, signing, identity, and public-publish effects remain high risk.
- Provider payloads must be sanitized before audit or memory storage.
- Existing direct effects are migration targets, not implicit v2 approval.

