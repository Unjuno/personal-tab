#!/usr/bin/env python3
"""Compatibility launcher; usable without an editable package installation."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
from personal_tab.server import main

if __name__ == "__main__":
    main()
