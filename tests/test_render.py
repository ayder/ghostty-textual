from __future__ import annotations

from ghostty_textual._render import RenderState
from ghostty_textual.cells import LinkInterner, StyleInterner
from tests.conftest import make_terminal


def test_text_wide_and_combining_cells(native) -> None:
    harness = make_terminal(native, cols=10, rows=2)
    state = RenderState(native)
    try:
        harness.feed("A界e\u0301".encode())
        state.update(harness.terminal)
        cells = state.read_rows(StyleInterner(), LinkInterner(), force=True)[0].cells
        assert cells[0].text == "A"
        assert (cells[1].text, cells[1].width) == ("界", 2)
        assert cells[2].width == 0
        assert cells[3].text == "e\u0301"
    finally:
        state.close()
        harness.close()


def test_dirty_rows_are_acknowledged_independently(native) -> None:
    harness = make_terminal(native, cols=10, rows=3)
    state = RenderState(native)
    styles, links = StyleInterner(), LinkInterner()
    try:
        harness.feed(b"a\r\nb")
        state.update(harness.terminal)
        state.read_rows(styles, links, force=True)
        state.clear_global_dirty()
        harness.feed(b"\x1b[1;1Hz")
        state.update(harness.terminal)
        assert [patch.y for patch in state.read_rows(styles, links, force=False)] == [0, 1]
        state.clear_global_dirty()
        state.update(harness.terminal)
        assert not state.is_dirty()
    finally:
        state.close()
        harness.close()


def test_cursor_uses_viewport_coordinates(native) -> None:
    harness = make_terminal(native, cols=10, rows=2)
    state = RenderState(native)
    try:
        harness.feed(b"abc")
        state.update(harness.terminal)
        cursor = state.read_cursor()
        assert (cursor.x, cursor.y, cursor.visible) == (3, 0, True)
    finally:
        state.close()
        harness.close()


def test_underline_kinds_colour_and_ambiguous_width(native) -> None:
    harness = make_terminal(native, cols=10, rows=2)
    state = RenderState(native)
    try:
        harness.feed(
            b"\x1b[4mA\x1b[4:2mB\x1b[4:3mC\x1b[4:4mD\x1b[4:5;58;2;1;2;3mE" + "\u00b7".encode()
        )
        state.update(harness.terminal)
        styles = StyleInterner()
        row = state.read_rows(styles, LinkInterner(), force=True)[0]
        assert [styles.resolve(cell.style_id).underline for cell in row.cells[:5]] == [
            1,
            2,
            3,
            4,
            5,
        ]
        assert styles.resolve(row.cells[4].style_id).underline_color == (1, 2, 3)
        assert (row.cells[5].text, row.cells[5].width) == ("\u00b7", 1)
    finally:
        state.close()
        harness.close()


def test_osc_palette_change_repaints_existing_cells() -> None:
    from ghostty_textual.emulator import Terminal

    with Terminal(10, 2) as terminal:
        terminal.feed(b"\x1b[31mA")
        before = terminal.snapshot()
        old_fg = before.styles[before.row_patches[0].cells[0].style_id].fg
        terminal.feed(b"\x1b]4;1;#123456\x07")
        after = terminal.snapshot()
        assert after is not None
        new_fg = after.styles[after.row_patches[0].cells[0].style_id].fg
        assert old_fg != new_fg == (18, 52, 86)
