"""What `ghostty_terminal_reset()` does and does not clear.

Spec v2 §3.5. The colour-override result is the reason `hard_reset()` frees and
recreates the terminal instead of calling the C reset: reconnect must not
inherit the dead session's palette.
"""

from __future__ import annotations

from ghostty_textual._native import Native
from tests.conftest import Harness, make_terminal


def _populate(harness: Harness) -> None:
    harness.feed(b"\x1b]0;my-title\x07")
    harness.feed(b"".join(b"line%d\r\n" % i for i in range(40)))


class TestClearedByReset:
    def test_title(self, native: Native) -> None:
        harness = make_terminal(native, rows=5, scrollback=1000)
        try:
            _populate(harness)
            assert harness.get_str("TITLE") == b"my-title"
            native.lib.ghostty_terminal_reset(harness.terminal)
            assert harness.get_str("TITLE") == b""
        finally:
            harness.close()

    def test_scrollback_and_total_rows(self, native: Native) -> None:
        harness = make_terminal(native, rows=5, scrollback=1000)
        try:
            _populate(harness)
            assert harness.get_u32("SCROLLBACK_ROWS") == 36
            assert harness.get_u32("TOTAL_ROWS") == 41
            native.lib.ghostty_terminal_reset(harness.terminal)
            assert harness.get_u32("SCROLLBACK_ROWS") == 0
            assert harness.get_u32("TOTAL_ROWS") == 5
        finally:
            harness.close()

    def test_alternate_screen(self, native: Native) -> None:
        harness = make_terminal(native, rows=5)
        try:
            harness.feed(b"\x1b[?1049h")
            assert harness.get_u32("ACTIVE_SCREEN") == 1
            native.lib.ghostty_terminal_reset(harness.terminal)
            assert harness.get_u32("ACTIVE_SCREEN") == 0
        finally:
            harness.close()


class TestPreservedByReset:
    def test_osc_colour_overrides_survive(self, native: Native) -> None:
        """The finding that decided `hard_reset()`.

        A remote `.bashrc` repainting the terminal would otherwise bleed its
        colours into the next session after reconnect.
        """
        harness = make_terminal(native)
        try:
            assert harness.get_u32("COLOR_FOREGROUND") is None, (
                "a fresh terminal should report no override"
            )
            harness.feed(b"\x1b]10;#ff0000\x07")
            harness.feed(b"\x1b]11;#00ff00\x07")
            before = (harness.get_u32("COLOR_FOREGROUND"), harness.get_u32("COLOR_BACKGROUND"))
            assert before == (0x0000FF, 0x00FF00)

            native.lib.ghostty_terminal_reset(harness.terminal)

            after = (harness.get_u32("COLOR_FOREGROUND"), harness.get_u32("COLOR_BACKGROUND"))
            assert after == before, (
                "libghostty now clears colour overrides on reset -- hard_reset() could "
                "stop free-and-recreating. Revisit spec v2 §3.5."
            )
        finally:
            harness.close()


class TestHardResetContract:
    def test_recreation_clears_everything_the_c_reset_does_not(self, native: Native) -> None:
        """`hard_reset()` is free-and-recreate; this is the behaviour it must produce."""
        first = make_terminal(native, rows=5, scrollback=1000)
        try:
            _populate(first)
            first.feed(b"\x1b]10;#ff0000\x07")
            assert first.get_u32("COLOR_FOREGROUND") == 0x0000FF
        finally:
            first.close()

        second = make_terminal(native, rows=5, scrollback=1000)
        try:
            assert second.get_str("TITLE") == b""
            assert second.get_u32("SCROLLBACK_ROWS") == 0
            assert second.get_u32("ACTIVE_SCREEN") == 0
            assert second.get_u32("COLOR_FOREGROUND") is None
        finally:
            second.close()

    def test_selection_is_absent_on_a_fresh_terminal(self, native: Native) -> None:
        """Documentation value only -- recreation makes the question moot."""
        harness = make_terminal(native)
        try:
            selection = harness.ffi.new("GhosttySelection*")
            selection.size = harness.ffi.sizeof("GhosttySelection")
            rc = harness.lib.ghostty_terminal_get(
                harness.terminal, harness.lib.GHOSTTY_TERMINAL_DATA_SELECTION, selection
            )
            assert rc != 0
        finally:
            harness.close()
