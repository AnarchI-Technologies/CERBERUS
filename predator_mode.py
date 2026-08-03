"""Workspace compatibility alias for the canonical predator-mode policy."""

import sys
from src import predator_mode as _implementation

sys.modules[__name__] = _implementation
