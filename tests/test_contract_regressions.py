from __future__ import annotations

import pytest

import ghostty_textual
from ghostty_textual import GhosttyError
from ghostty_textual._render import RenderState
from ghostty_textual.cells import LinkInterner, StyleInterner
from ghostty_textual.emulator import Terminal
from tests.conftest import make_terminal


def test_same_row_cursor_move_produces_cursor_only_frame() -> None:
    with Terminal(20, 3) as terminal:
        terminal.feed(b"abcdef")
        terminal.snapshot()
        terminal.feed(b"\x1b[3D")
        frame = terminal.snapshot()
        assert frame is not None
        assert frame.row_patches == ()
        assert (frame.cursor.x, frame.cursor.y) == (3, 0)


@pytest.mark.parametrize(
    ("sequence", "attribute", "expected"),
    [
        (b"\x1b]10;#ff00ff\x07", "fg", (255, 0, 255)),
        (b"\x1b]11;#102030\x07", "bg", (16, 32, 48)),
    ],
)
def test_osc_default_colour_change_forces_frame(
    sequence: bytes, attribute: str, expected: tuple[int, int, int]
) -> None:
    with Terminal(20, 3) as terminal:
        terminal.feed(b"x")
        terminal.snapshot()
        terminal.feed(sequence)
        frame = terminal.snapshot()
        assert frame is not None and frame.full_redraw
        cell = frame.row_patches[0].cells[0]
        assert getattr(frame.styles[cell.style_id], attribute) == expected


def test_failed_hard_reset_poison_is_always_typed(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = Terminal(20, 3)

    def fail_create() -> None:
        raise TypeError("construction exploded")

    monkeypatch.setattr(terminal, "_create_native", fail_create)
    with pytest.raises(GhosttyError, match="hard reset failed"):
        terminal.hard_reset()
    with pytest.raises(GhosttyError, match="hard reset failed"):
        terminal.feed(b"x")
    terminal.close()


def test_callback_exception_is_re_raised_as_ghostty_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with Terminal(20, 3) as terminal:

        def fail_notify(notification: object) -> None:
            raise ValueError("callback exploded")

        monkeypatch.setattr(terminal, "_notify", fail_notify)
        with pytest.raises(GhosttyError, match="terminal callback failed"):
            terminal.feed(b"\x07")


def test_spacer_head_is_a_styled_padding_cell() -> None:
    with Terminal(6, 2) as terminal:
        terminal.feed(b"\x1b[44mabcde" + "字".encode())
        frame = terminal.snapshot(force=True)
        head = frame.row_patches[0].cells[-1]
        assert (head.text, head.width) == ("", 1)
        assert frame.styles[head.style_id].bg == (84, 84, 204)


def test_wide_tail_cursor_state_is_preserved() -> None:
    with Terminal(6, 2) as terminal:
        terminal.feed("界".encode())
        terminal.snapshot()
        terminal.feed(b"\x1b[1;2H")
        frame = terminal.snapshot()
        assert frame is not None
        assert (frame.cursor.x, frame.cursor.wide_tail) == (1, True)


def test_row_wrap_metadata_is_public() -> None:
    with Terminal(10, 3) as terminal:
        terminal.feed(b"ABCDEFGHIJKLMNOPQRST")
        frame = terminal.snapshot(force=True)
        assert frame.row_patches[0].wrapped
        assert not frame.row_patches[1].wrapped


def test_dirty_rows_survive_an_exception_mid_extraction(
    native, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = make_terminal(native, cols=10, rows=3)
    state = RenderState(native)
    styles, links = StyleInterner(), LinkInterner()
    original = state._read_row
    calls = 0

    def fail_second(y, style_interner, link_interner):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ValueError("copy failed")
        return original(y, style_interner, link_interner)

    try:
        harness.feed(b"a\r\nb")
        state.update(harness.terminal)
        monkeypatch.setattr(state, "_read_row", fail_second)
        with pytest.raises(ValueError, match="copy failed"):
            state.read_rows(styles, links, force=True)
        monkeypatch.setattr(state, "_read_row", original)
        state.update(harness.terminal)
        assert state.read_rows(styles, links, force=False)
    finally:
        state.close()
        harness.close()


def test_compatibility_sequences_and_split_utf8() -> None:
    with Terminal(10, 2) as terminal:
        terminal.feed(b"\x1b[?4m")
        terminal.feed(b"\xe2\x94")
        terminal.feed(b"\x80")
        frame = terminal.snapshot(force=True)
        assert frame.row_patches[0].cells[0].text == "─"


def test_frame_contract_types_are_publicly_exported() -> None:
    for name in (
        "BellRang",
        "ClipboardWritten",
        "CursorState",
        "ProcessingError",
        "RowPatch",
        "TerminalNotification",
        "TitleChanged",
        "ViewportState",
    ):
        assert name in ghostty_textual.__all__
        assert getattr(ghostty_textual, name) is not None
