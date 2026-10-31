"""Allow `python -m gatecheck`."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
