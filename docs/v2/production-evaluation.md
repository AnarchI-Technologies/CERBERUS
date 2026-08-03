# Local production evaluation protocol

CERBERUS records a sanitized sample every five minutes for the controlled
evaluation window. Samples contain health, runtime freshness, Claw version,
bounded action-result counts, shadow-policy outcomes, and deterministic
post-mortem categories. They explicitly exclude credentials and wallet data.
The operator-node profile reads Hellion's configured runtime state from
`/var/data/.cerberus`; it does not move or duplicate the memory store.

Production proof requires:

- at least 72 continuous hours of samples;
- health success rate of at least 99%;
- no unclassified service restart loop;
- no duplicate consequential action after retry or restart;
- complete policy-shadow coverage before enforcement is enabled;
- no secret or wallet material in evidence;
- rollback and recovery within five minutes;
- operator review before any candidate strategy or model is promoted.

Shadow records are evidence only. A passing report does not automatically merge
a branch, enable policy enforcement, promote an Ollama model, or authorize a
second live agent.
