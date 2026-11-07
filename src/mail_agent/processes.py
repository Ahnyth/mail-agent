"""Starts helper programs (Claude Code, Tesseract, PowerShell) without a console window.

The agent runs invisibly in the background (``background.py``, started with ``pythonw.exe``). Windows gives
every console program started from such a process its own window, so each mail analysis would flash a black
window. ``CREATE_NO_WINDOW`` prevents that; on other systems the flag does not exist and nothing is passed.
"""

import subprocess
from typing import Any

NO_WINDOW: dict[str, Any] = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
)
"""Extra arguments for ``subprocess.run`` so that no console window opens."""
