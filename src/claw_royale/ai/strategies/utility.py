"""Compatibility alias for the extracted Claw Royale utility strategy module."""

import sys
from anarchi_claw_strategies.strategies import utility as _implementation

sys.modules[__name__] = _implementation