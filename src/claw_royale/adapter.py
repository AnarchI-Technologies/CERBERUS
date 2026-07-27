"""Compatibility alias for the extracted Claw Royale adapter."""

import sys
from anarchi_claw_royale_adapter import adapter as _implementation

sys.modules[__name__] = _implementation