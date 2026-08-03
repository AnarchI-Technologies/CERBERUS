"""Workspace compatibility alias for the canonical death-zone engine."""

import sys
from src import death_zone_engine as _implementation

sys.modules[__name__] = _implementation
