"""samantha public API."""
from __future__ import annotations

import os

from ._boot import arm, resident

__version__ = "1.0.0"

if os.getenv("SAMANTHA") != "off":
    try:
        arm()
    except Exception:  # Importing samantha must never break the host.
        pass
