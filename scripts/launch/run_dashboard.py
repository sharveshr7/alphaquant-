"""Root launcher for AlphaQuant AI Pro Trading Dashboard."""

import sys
from pathlib import Path

# Ensure repository root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dashboard.run_dashboard import main

if __name__ == "__main__":
    main()
