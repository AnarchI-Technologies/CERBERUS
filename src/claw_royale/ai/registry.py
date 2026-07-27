"""Compatibility alias for the extracted Claw Royale strategy registry."""

import sys
from anarchi_claw_strategies import registry as _implementation

sys.modules[__name__] = _implementation