# Local Windows operator node

CERBERUS v2 is operator-node-first. Ollama must remain local and must not be
exposed by port forwarding or a public reverse proxy.

## Measured node profile — 2026-07-18

- Windows 11 Home 64-bit, build 10.0.26200
- Intel Core Ultra 7 155U, 12 cores / 14 logical processors
- 15.4 GB total system RAM; free RAM is workload-dependent (3.2 GB at the latest
  measurement)
- Integrated Intel Graphics; Windows reports 2 GB adapter memory
- 951.6 GB system volume with 800.8 GB free
- Ollama 0.32.1 at the default local endpoint `127.0.0.1:11434`

Disk capacity is sufficient for multiple small evaluation models. Runtime
policy must nevertheless be based on RAM, model load latency, and worker
pressure, not disk capacity alone.

## Containment profile

- Keep Ollama bound to localhost.
- Do not place wallet keys, tokens, raw OAuth payloads, or unrestricted memory in
  prompts.
- Keep aliases in `models/aliases.json`; application modules must not hardcode
  model tags.
- Treat every model output as untrusted structured input.
- Keep `CERBERUS_MODEL_GATEWAY_ENABLED=false` until the gateway and evaluation
  suite are merged and production-proven.
- A failed health check or deadline must select deterministic no-model behavior.

## Installed evaluation candidates

```text
qwen3:1.7b  1.4 GB
qwen3:4b    2.5 GB
```

Neither candidate is approved for production decisions. See the recorded
evaluation for the current result.

