#!/usr/bin/env python3
"""Standalone entry point — works regardless of the cloned directory name."""
import sys
from pathlib import Path

# Add the repo dir to sys.path so the flat imports in main.py (tracker, switcher, etc.)
# are resolvable when this script is invoked directly rather than via -m.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as _main  # noqa: E402

_main.main()
