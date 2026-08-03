"""Compatibility alias for the extracted Claw Royale social strategy module."""

import sys
from anarchi_claw_strategies.strategies import social as _implementation

sys.modules[__name__] = _implementation