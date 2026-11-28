"""Vannus — image dataset acquisition workbench."""

import sys
from pathlib import Path

FALLBACK_VERSION = "0.1.0"


def _resolve_version() -> str:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            version_file = Path(meipass) / "BUILD_VERSION"
            if version_file.is_file():
                value = version_file.read_text(encoding="utf-8").strip()
                if value:
                    return value
    return FALLBACK_VERSION


__version__ = _resolve_version()
