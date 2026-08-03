"""Workspace compatibility alias for the canonical threat engine."""

import sys
from src import threat_engine as _implementation

sys.modules[__name__] = _implementation
