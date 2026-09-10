"""Textual terminal widget backed by libghostty-vt.

Importing this package never loads the native library -- see spec v2 §7.
`GhosttyUnavailable` is raised when a `Terminal` is constructed, so docs, type
checking, and applications with optional terminal features stay importable on
platforms with no wheel.
"""

from __future__ import annotations

from importlib.metadata import version as _version

from ghostty_textual._native import GhosttyError, GhosttyUnavailable
from ghostty_textual._render import CursorState, RowPatch, ViewportState
from ghostty_textual.cells import Cell, CellStyle
from ghostty_textual.emulator import (
    BellRang,
    ClipboardPolicy,
    ClipboardWritten,
    Frame,
    ProcessingError,
    ResourceLimits,
    ScrollBottom,
    ScrollDelta,
    ScrollTop,
    ScrollToRow,
    Terminal,
    TerminalEffects,
    TerminalModes,
    TerminalNotification,
    TitleChanged,
)
from ghostty_textual.keys import KeyEvent, MouseEvent
from ghostty_textual.theme import TerminalTheme
from ghostty_textual.widget import PasteRejected, TerminalView

__all__ = [
    "Cell",
    "CellStyle",
    "BellRang",
    "ClipboardPolicy",
    "ClipboardWritten",
    "CursorState",
    "Frame",
    "GhosttyError",
    "GhosttyUnavailable",
    "KeyEvent",
    "MouseEvent",
    "PasteRejected",
    "ProcessingError",
    "ResourceLimits",
    "RowPatch",
    "ScrollBottom",
    "ScrollDelta",
    "ScrollToRow",
    "ScrollTop",
    "Terminal",
    "TerminalEffects",
    "TerminalModes",
    "TerminalNotification",
    "TerminalTheme",
    "TerminalView",
    "TitleChanged",
    "ViewportState",
    "__version__",
]

# Build metadata is generated from [project].version in pyproject.toml.
__version__ = _version("ghostty-textual")
