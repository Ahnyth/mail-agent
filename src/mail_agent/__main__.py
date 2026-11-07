"""Runs the command line with ``python -m mail_agent``, the same as ``mail-agent``."""

import sys

from .cli import main

sys.exit(main())
