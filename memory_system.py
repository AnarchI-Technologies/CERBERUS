"""Workspace compatibility alias for the canonical memory package."""

import sys
from data import memory_system as _implementation

sys.modules[__name__] = _implementation
