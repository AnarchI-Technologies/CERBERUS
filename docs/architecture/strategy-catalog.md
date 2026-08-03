# ClawRoyale.ai Strategy Catalog

Every literal legacy intent now has a stable `clawroyale.<intent>` identifier.
The catalog is owned by the independent ClawRoyale.ai package and imports no
legacy implementation.

A temporary compatibility registry makes every strategy callable by ID. It
uses cheap state-signal gates and caches provider evaluation, so multiple
strategies from one legacy cortex do not repeat the cortex computation.

This is the migration seam, not the final extraction. Strategy families can now
move behind their stable IDs one at a time, with registry and parity contracts
unchanged. The live core loop is not switched in this wave.
