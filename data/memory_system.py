"""Compatibility alias for AnarchI Memory's canonical memory system."""

import sys
from anarchi_memory import memory_system as _implementation

sys.modules[__name__] = _implementation