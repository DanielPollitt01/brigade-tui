"""Brigade.

A read-only terminal view over a running software factory. This package is the
scaffold: the app, its tab bar, and its key bindings.
"""

from brigade_tui.app import DEFAULT_PROJECTS, BrigadeTUI, main

__all__ = ["DEFAULT_PROJECTS", "BrigadeTUI", "main"]
