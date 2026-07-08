"""
Computer control agent config.
"""

from __future__ import annotations

import os

SCREENSHOT_DIR = os.environ.get("SARVOS_SCREENSHOT_DIR", "sarvos_workspace/screenshots")

# How much to change volume/brightness by for the up/down (non-percentage)
# operations, as a percentage point step.
VOLUME_STEP = int(os.environ.get("SARVOS_VOLUME_STEP", "10"))
BRIGHTNESS_STEP = int(os.environ.get("SARVOS_BRIGHTNESS_STEP", "10"))
