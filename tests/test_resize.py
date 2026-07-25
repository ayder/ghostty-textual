"""Resize with unknown pixel dimensions, and the XTWINOPS replies that follow.

Spec v2 §3.4. A Textual cell renderer cannot know physical pixels. Reporting
zero is honest; inventing 8x16 (as `pyghostty.core` does) makes the terminal lie
to every application that asks.
"""

from __future__ import annotations

import pytest

from ghostty_textual._native import Native
from tests.conftest import Harness


class TestResize:
    def test_zero_pixel_dimensions_are_accepted(self, native: Native, terminal: Harness) -> None:
        rc = native.lib.ghostty_terminal_resize(terminal.terminal, 100, 30, 0, 0)
        assert rc == 0
        assert terminal.get_u32("COLS") == 100
        assert terminal.get_u32("ROWS") == 30
        assert terminal.get_u32("WIDTH_PX") == 0
        assert terminal.get_u32("HEIGHT_PX") == 0

    def test_nonzero_cell_size_derives_pixel_size(self, native: Native, terminal: Harness) -> None:
        """Shown for contrast: this is the lie we decline to tell."""
        assert native.lib.ghostty_terminal_resize(terminal.terminal, 100, 30, 8, 16) == 0
        assert terminal.get_u32("WIDTH_PX") == 800
        assert terminal.get_u32("HEIGHT_PX") == 480


class TestSizeReports:
    """XTWINOPS needs its own callback -- resizing alone answers nothing."""

    @pytest.mark.parametrize(
        ("name", "sequence"),
        [
            ("CSI 14t text area px", b"\x1b[14t"),
            ("CSI 16t cell size px", b"\x1b[16t"),
            ("CSI 18t text area chars", b"\x1b[18t"),
        ],
    )
    def test_silent_without_size_callback(
        self, terminal: Harness, name: str, sequence: bytes
    ) -> None:
        assert terminal.reply_to(sequence) == [], f"{name} answered without OPT_SIZE"

    def test_text_area_in_characters(self, sized_terminal: Harness) -> None:
        assert sized_terminal.reply_to(b"\x1b[18t") == [b"\x1b[8;24;80t"]

    def test_text_area_in_pixels_is_honestly_zero(self, sized_terminal: Harness) -> None:
        assert sized_terminal.reply_to(b"\x1b[14t") == [b"\x1b[4;0;0t"]

    def test_cell_size_in_pixels_is_honestly_zero(self, sized_terminal: Harness) -> None:
        assert sized_terminal.reply_to(b"\x1b[16t") == [b"\x1b[6;0;0t"]

    def test_size_callback_is_actually_invoked(self, sized_terminal: Harness) -> None:
        sized_terminal.size_calls.clear()
        sized_terminal.feed(b"\x1b[18t")
        assert len(sized_terminal.size_calls) == 1
