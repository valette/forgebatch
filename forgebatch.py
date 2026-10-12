#!/usr/bin/env python3
"""forgebatch launcher - runs the modular Textual application."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from app import main  # noqa: E402

if __name__ == "__main__":
    main()
