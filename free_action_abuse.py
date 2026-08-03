"""Workspace compatibility alias for canonical free-action strategies."""

import sys
from src import free_action_abuse as _implementation

sys.modules[__name__] = _implementation
