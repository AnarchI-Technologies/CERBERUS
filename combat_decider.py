"""Workspace compatibility alias for the canonical combat decider."""

import sys
from src import combat_decider as _implementation

sys.modules[__name__] = _implementation
