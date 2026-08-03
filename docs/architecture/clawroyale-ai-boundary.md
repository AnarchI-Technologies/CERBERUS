# ClawRoyale.ai Independent Boundary

The ClawRoyale.ai package is the first consumer of `anarchi.interop.v1`.

It owns provider capability names, provider action validation, safe observation
normalization, provider frame translation, outcome normalization, and strategy
registration. Network, signing, persistence, and process lifecycle are injected
or remain outside this package.

The package imports only the interoperability kernel and the Python standard
library. It does not import the CERBERUS core loop, runtime state, `TurnState`,
the transitional game-adapter package, or the current live Claw runtime.

No existing execution path is switched by this wave.
