"""
SV-PRO Backend package.
"""

# Expose submodules for test patching convenience (tests patch `backend.database.*`).
from . import database  # noqa: F401
