"""Compatibility alias for the extracted Claw Royale combat strategy module."""

import sys
from anarchi_claw_strategies.strategies import combat as _implementation

sys.modules[__name__] = _implementation