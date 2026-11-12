"""AlphaQuant AI Pro - Centralized Configuration Bridge.

Exposes application settings and path constants for dashboard and tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.backend.config import Config, config

# Centralized Directory Constants
DATA_DIR = PROJECT_ROOT / "data"
RUNTIME_DIR = PROJECT_ROOT / "runtime"
CACHE_DIR = RUNTIME_DIR / "cache"
LOGS_DIR = RUNTIME_DIR / "logs"
TEMPORARY_DIR = RUNTIME_DIR / "temporary"
DOCS_DIR = PROJECT_ROOT / "docs"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
CONFIG_DIR = PROJECT_ROOT / "config"
TESTS_DIR = PROJECT_ROOT / "tests"

__all__ = [
    "Config",
    "config",
    "PROJECT_ROOT",
    "DATA_DIR",
    "RUNTIME_DIR",
    "CACHE_DIR",
    "LOGS_DIR",
    "TEMPORARY_DIR",
    "DOCS_DIR",
    "SCREENSHOTS_DIR",
    "CONFIG_DIR",
    "TESTS_DIR",
]
