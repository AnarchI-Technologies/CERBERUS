"""Compatibility alias for Anar Vault's canonical encrypted storage."""

import sys
from anar_vault import vault as _implementation

sys.modules[__name__] = _implementation