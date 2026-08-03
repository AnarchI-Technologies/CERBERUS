"""Workspace compatibility alias for the canonical EP economy engine."""

import sys
from src import ep_economy_engine as _implementation

sys.modules[__name__] = _implementation
