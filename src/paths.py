"""Keep bundled resources separate from files the desk can change."""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_BUILD = getattr(sys, "frozen", False) and sys.platform == "win32"
if DESKTOP_BUILD:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    DATA_DIR = local / "Morning Deal Brief"
    ENV_FILE = DATA_DIR / ".env"
    CACHE_DIR = DATA_DIR / "cache"
else:
    DATA_DIR = PROJECT_ROOT / "var"
    ENV_FILE = PROJECT_ROOT / ".env"
    CACHE_DIR = PROJECT_ROOT / ".cache"
