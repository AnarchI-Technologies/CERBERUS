"""Compatibility alias for the extracted Claw Royale quest strategy module."""

import sys
from anarchi_claw_strategies.strategies import quest as _implementation

sys.modules[__name__] = _implementation