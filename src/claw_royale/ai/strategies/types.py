"""Compatibility alias for the extracted Claw Royale types strategy module."""

import sys
from anarchi_claw_strategies.strategies import types as _implementation

sys.modules[__name__] = _implementation