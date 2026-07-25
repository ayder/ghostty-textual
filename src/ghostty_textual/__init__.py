"""Textual terminal widget backed by libghostty-vt.

Importing this package never loads the native library -- see spec v2 §7.
`GhosttyUnavailable` is raised when a `Terminal` is constructed, so docs, type
checking, and applications with optional terminal features stay importable on
platforms with no wheel.
"""

from __future__ import annotations

from ghostty_textual._native import GhosttyError, GhosttyUnavailable

__all__ = ["GhosttyError", "GhosttyUnavailable", "__version__"]

__version__ = "0.0.1"
