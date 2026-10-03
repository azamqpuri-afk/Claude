"""Entry point for the compiled Windows program (SmartLayer.exe). Same as  python -m smart_layer."""

import sys

from smart_layer.cli import main

if __name__ == "__main__":
    sys.exit(main())
