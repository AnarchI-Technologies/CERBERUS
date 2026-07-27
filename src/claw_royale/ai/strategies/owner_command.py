"""Compatibility alias for the extracted Claw Royale owner_command strategy module."""

import sys
from anarchi_claw_strategies.strategies import owner_command as _implementation

sys.modules[__name__] = _implementation