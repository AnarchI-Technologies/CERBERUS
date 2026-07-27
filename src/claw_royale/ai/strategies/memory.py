"""Compatibility alias for the extracted Claw Royale memory strategy module."""

import sys
from anarchi_claw_strategies.strategies import memory as _implementation

sys.modules[__name__] = _implementation