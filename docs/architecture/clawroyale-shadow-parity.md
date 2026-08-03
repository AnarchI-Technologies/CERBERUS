# ClawRoyale.ai Shadow Parity

This compatibility seam compares sanitized provider inputs across the legacy
and portable boundaries. It is read-only and cannot invoke transport.

Observation parity currently covers match identity, turn, effective action
availability, actor identity, location identity, and alive count. Action parity
covers accepted, missing-field, and unsupported action cases.

The bridge deliberately imports both sides and is temporary. Neither independent
package imports the bridge. No live execution route is changed.
