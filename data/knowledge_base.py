"""Compatibility alias for AnarchI Memory's knowledge base."""

import sys
from anarchi_memory import knowledge_base as _implementation

sys.modules[__name__] = _implementation