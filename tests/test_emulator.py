from __future__ import annotations

import threading

import pytest

from ghostty_textual.emulator import (
    BellRang,
    KeyEvent,
    ResourceLimits,
    Terminal,
    TitleChanged,
)


def test_effects_title_bell_and_query_order() -> None:
    with Terminal(20, 3) as terminal:
        effects = terminal.feed(b"\x1b]0;hello\x07\x07\x1b[6n\x1b[c")
        assert effects.pty_writes == (b"\x1b[1;1R", b"\x1b[?62;22c")
        assert TitleChanged("hello") in effects.notifications
        assert BellRang() in effects.notifications


def test_snapshot_partial_force_and_clean() -> None:
    with Terminal(20, 3) as terminal:
        terminal.feed(b"a\r\nb")
        first = terminal.snapshot()
        assert first is not None and first.full_redraw
        assert terminal.snapshot() is None
        terminal.feed(b"\x1b[1;1Hz")
        patch = terminal.snapshot()
        # Moving the cursor dirties both its old and new row.
        assert patch is not None and [row.y for row in patch.row_patches] == [0, 1]
        assert len(terminal.snapshot(force=True).row_patches) == 3


def test_style_rollover_is_atomic() -> None:
    with Terminal(2, 1, limits=ResourceLimits(max_interned_styles=2)) as terminal:
        terminal.feed(b"\x1b[38;2;10;0;0mA")
        terminal.snapshot()
        terminal.feed(b"\x1b[38;2;20;0;0mB")
        first = terminal.snapshot()
        terminal.feed(b"\x1b[1;1H\x1b[38;2;30;0;0mC")
        second = terminal.snapshot()
        assert second.generation > first.generation
        assert second.full_redraw
        assert all(
            0 <= cell.style_id < len(second.styles)
            for row in second.row_patches
            for cell in row.cells
        )


def test_viewport_and_hard_reset() -> None:
    with Terminal(20, 5, scrollback=20) as terminal:
        terminal.feed(b"\x1b]0;old\x07")
        terminal.feed(b"".join(b"line%d\r\n" % index for index in range(100)))
        assert terminal.viewport.at_bottom
        assert terminal.viewport.scrollback_rows > 0
        terminal.scroll_to_top()
        assert not terminal.viewport.at_bottom
        terminal.scroll_to_bottom()
        assert terminal.viewport.at_bottom
        terminal.hard_reset()
        assert terminal.title in (None, "")
        assert terminal.viewport.scrollback_rows == 0
        assert terminal.snapshot(force=True).generation == 0


def test_modes_and_encoders() -> None:
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent("up")) == b"\x1b[A"
        terminal.feed(b"\x1b[?1h\x1b[?2004h\x1b[?1004h")
        assert terminal.modes.app_cursor_keys
        assert terminal.encode_key(KeyEvent("up")) == b"\x1bOA"
        assert terminal.encode_paste("a\nb") == b"\x1b[200~a\nb\x1b[201~"
        assert terminal.encode_focus(True) == b"\x1b[I"


def test_unsafe_unbracketed_paste_is_rejected() -> None:
    with Terminal(20, 3) as terminal:
        assert terminal.encode_paste("echo unsafe\n") is None


def test_printable_punctuation_uses_utf8_fallback() -> None:
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent("exclamation_mark", "!")) == b"!"


def test_operations_after_close_and_cross_thread_are_rejected() -> None:
    terminal = Terminal(20, 3)
    errors: list[BaseException] = []

    def other() -> None:
        try:
            terminal.feed(b"x")
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=other)
    thread.start()
    thread.join()
    assert errors and isinstance(errors[0], RuntimeError)
    terminal.close()
    terminal.close()
    with pytest.raises(RuntimeError):
        terminal.feed(b"x")


def test_effective_style_limit_tracks_resize() -> None:
    with Terminal(10, 2, limits=ResourceLimits(max_interned_styles=1)) as terminal:
        assert terminal.effective_style_limit == 20
        terminal.resize(100, 40)
        assert terminal.effective_style_limit == 4000


def test_hyperlinks_are_frame_scoped() -> None:
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\")
        frame = terminal.snapshot()
        assert frame.links == ("https://example.com",)
        assert frame.row_patches[0].cells[0].link_id == 0


def _visible_text(terminal: Terminal) -> tuple[str, ...]:
    frame = terminal.snapshot(force=True)
    return tuple(
        "".join(cell.text or " " for cell in row.cells if cell.width).rstrip()
        for row in frame.row_patches
    )


def test_scrolled_viewport_stays_anchored_and_bottom_follow_resumes() -> None:
    with Terminal(12, 4, scrollback=1000) as terminal:
        terminal.feed(b"".join(b"line%02d\r\n" % index for index in range(20)))
        bottom = _visible_text(terminal)
        terminal.scroll_to_top()
        anchored = _visible_text(terminal)

        terminal.feed(b"line20\r\nline21\r\n")
        assert _visible_text(terminal) == anchored
        assert not terminal.viewport.at_bottom

        terminal.resize(14, 5)
        assert _visible_text(terminal)[0] == anchored[0]
        terminal.scroll_to_bottom()
        assert terminal.viewport.at_bottom
        assert _visible_text(terminal) != bottom


def test_encoder_reloads_modify_other_keys_and_kitty_modes() -> None:
    with Terminal(20, 3) as terminal:
        shifted_tab = KeyEvent("tab", shift=True)
        assert terminal.encode_key(shifted_tab) == b"\x1b[Z"

        terminal.feed(b"\x1b[>4;2m")
        assert terminal.encode_key(shifted_tab) == b"\x1b[27;2;9~"

        terminal.feed(b"\x1b[>1u")
        assert terminal.encode_key(shifted_tab) == b"\x1b[9;2u"


def test_focus_lost_and_numpad_key_names_are_supported() -> None:
    with Terminal(20, 3) as terminal:
        assert terminal.encode_focus(False) is None
        terminal.feed(b"\x1b[?1004h")
        assert terminal.encode_focus(False) == b"\x1b[O"
        assert terminal.encode_key(KeyEvent("numpad_1", "1")) == b"1"
