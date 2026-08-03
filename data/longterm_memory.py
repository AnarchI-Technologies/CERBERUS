"""Compatibility alias for AnarchI Memory's long-term storage."""

import sys
from anarchi_memory import longterm_memory as _implementation

sys.modules[__name__] = _implementation