"""Compatibility alias for AnarchI Memory's Mongo memory backend."""

import sys
from anarchi_memory import mongo_memory as _implementation

sys.modules[__name__] = _implementation