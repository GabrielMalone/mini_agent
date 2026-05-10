#!/usr/bin/env python3
"""
terminal.py — ANSI colour helpers for mini_agent output.

Colours are automatically disabled when stderr is not a TTY or the user
passes ``--no-color``.
"""

import sys

# Enabled when stderr is a terminal and user didn't pass --no-color
_ENABLED = sys.stderr.isatty() and "--no-color" not in sys.argv

_RESET  = "\033[0m"
_DIM    = "\033[2m"
_RED    = "\033[31m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_CYAN   = "\033[36m"


def c(text: str, code: str) -> str:
    """Wrap *text* in an ANSI colour code, stripping when colours are off."""
    if _ENABLED:
        return f"{code}{text}{_RESET}"
    return text
