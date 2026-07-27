"""Compatibility alias for the extracted Claw Royale progression strategy module."""

import sys
from anarchi_claw_strategies.strategies import progression as _implementation

sys.modules[__name__] = _implementation