"""Compatibility alias for the extracted Claw Royale learned_policy strategy module."""

import sys
from anarchi_claw_strategies.strategies import learned_policy as _implementation

sys.modules[__name__] = _implementation