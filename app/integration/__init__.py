"""Integration client layer for external services."""

# Ensure PyInstaller bundles bridge management helpers in frozen builds.
from . import ce_bridge_manager  # noqa: F401
