"""Compatibility alias for AnarchI Memory's knowledge compactor."""

import sys
from anarchi_memory import knowledge_compactor as _implementation

sys.modules[__name__] = _implementation