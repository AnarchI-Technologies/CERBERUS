"""Compatibility alias for the extracted Claw Royale economy strategy module."""

import sys
from anarchi_claw_strategies.strategies import economy as _implementation

sys.modules[__name__] = _implementation