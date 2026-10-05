from __future__ import annotations

from dataclasses import replace

import pytest

from ghostty_textual.cells import CellStyle
from ghostty_textual.emulator import ResourceLimits, ScrollDelta, Terminal
from ghostty_textual.theme import DEFAULT_THEME
from ghostty_textual.widget import TerminalView


async def send(data: bytes) -> None:
    pass


def test_rich_style_cache_reuses_values_without_mutating_base_style() -> None:
    convert = TerminalView._rich_style
    convert.cache_clear()
    style = CellStyle(fg=(1, 2, 3), bg=(4, 5, 6), bold=True)
    base = convert(style)
    assert convert(replace(style)) is base
    selected = convert(style, "reverse")
    assert selected.reverse
    assert not base.reverse
    assert convert(style, "reverse") is selected
    underline = convert(style, "underline")
    assert underline.underline and not base.underline
    assert convert.cache_info().misses == 3
    assert convert.cache_info().maxsize == 4096


def test_selection_direction_cursor_phase_and_wide_tail_preserve_overlays() -> None:
    with Terminal(6, 3) as terminal:
        terminal.feed("a界b\r\ncdef\r\nghij".encode())
        view = TerminalView(send=send, terminal=terminal)
        view._apply_frame(terminal.snapshot(force=True))
        view._selection_anchor = (1, 0)
        view._selection_end = (2, 2)
        forward = [view.render_line(y) for y in range(3)]
        view._selection_anchor, view._selection_end = (view._selection_end, view._selection_anchor)
        assert [view.render_line(y) for y in range(3)] == forward
        assert forward[0]._segments[0].text == "a"
        assert not forward[0]._segments[0].style.reverse
        assert forward[0]._segments[1].text.startswith("界b")
        assert forward[0]._segments[1].style.reverse
        assert all(segment.style.reverse for segment in forward[1])
        assert forward[2]._segments[0].text == "ghi"
        assert not forward[2]._segments[-1].style.reverse
        view._focused = True
        view._cursor = replace(
            view._cursor, x=2, y=0, visible=True, blinking=True, wide_tail=True, style=2
        )
        assert view.render_line(0)._segments[1].text == "界"
        assert view.render_line(0)._segments[1].style.underline
        view._cursor_phase = False
        assert view.render_line(0) == forward[0]
        view._focused = False
        view._selection_anchor = view._selection_end = None
        assert not any(segment.style.reverse for segment in view.render_line(0))


def test_rich_styles_follow_theme_and_reused_interner_ids() -> None:
    with Terminal(6, 2) as terminal:
        view = TerminalView(send=send, terminal=terminal)
        terminal.feed(b"x")
        view._apply_frame(terminal.snapshot())
        before = view.render_line(0)._segments[0].style
        terminal.hard_reset()
        terminal.set_theme(replace(DEFAULT_THEME, foreground=(1, 2, 3)))
        terminal.feed(b"y")
        view._apply_frame(terminal.snapshot())
        after = view.render_line(0)._segments[0].style
        assert before != after
        assert after.color.triplet == (1, 2, 3)


def test_native_style_cache_matches_uncached_extraction_across_updates() -> None:
    class NoCache(dict):
        def get(self, key, default=None):
            return None

    with Terminal(12, 4) as cached, Terminal(12, 4) as uncached:
        uncached._render._style_cache = NoCache()
        sequences = [
            b"\x1b[1;3;4:3;58;5;1mabc\x1b[0m\r\n",
            b"\x1b[31;44mxyz\x1b[0m\x1b[42m\x1b[K\r\n",
            b"\x1b[38;2;1;2;3;48;2;4;5;6m" + "界e\u0301".encode(),
            b"\x1b[0m\x1b[7;8;9;53mQ\r\n\x1b[0m",
            b"\x1b]4;1;#123456\x07\x1b]10;#654321\x07",
            b"\x1b[?1049h\x1b[32malt\x1b[?1049l",
            b"\r\n" * 8 + b"\x1b[35mend",
        ]
        for sequence in sequences:
            cached.feed(sequence)
            uncached.feed(sequence)
            assert cached.snapshot(force=True) == uncached.snapshot(force=True)
        for terminal in (cached, uncached):
            terminal.resize(8, 5)
            terminal.scroll_viewport(ScrollDelta(-2))
        assert cached.snapshot(force=True) == uncached.snapshot(force=True)


def test_repeated_native_styles_reuse_conversion_but_palette_updates_invalidate() -> None:
    with Terminal(12, 2) as terminal:
        terminal.feed(b"\x1b[31mxxxxxxxxxxxx")
        before = terminal.snapshot()
        assert len(terminal._render._style_cache) == 1
        style = before.styles[before.row_patches[0].cells[0].style_id]
        terminal.feed(b"\x1b]4;1;#123456\x07")
        after = terminal.snapshot()
        updated = after.styles[after.row_patches[0].cells[0].style_id]
        assert updated != style
        assert updated.fg == (18, 52, 86)


def test_link_scratch_buffer_grows_reuses_and_rejects_oversized_uris() -> None:
    limits = ResourceLimits(max_link_uri_bytes=512)
    with Terminal(20, 3, limits=limits) as terminal:
        initial = terminal._render._link_buffer
        for length in (30, 256, 512, 513, 4096, 30):
            uri = "https://example.com/".ljust(length, "x")
            terminal.feed(b"\x1b[1;1H\x1b]8;;" + uri.encode() + b"\x1b\\X\x1b]8;;\x1b\\")
            frame = terminal.snapshot()
            cell = frame.row_patches[0].cells[0]
            if length <= limits.max_link_uri_bytes:
                assert frame.links[cell.link_id] == uri
            else:
                assert cell.link_id is None
            if length <= 256:
                if terminal._render._link_capacity == 256:
                    assert terminal._render._link_buffer is initial
            assert terminal._render._link_capacity <= 512
        assert terminal._render._link_capacity == 512


def test_long_grapheme_buffer_growth_preserves_styled_cells() -> None:
    with Terminal(10, 2) as terminal:
        # 64 suffix codepoints respect the native cap; four-byte combining
        # characters still require 257 bytes and grow the 256-byte buffer.
        text = "e" + "\U0001d185" * 64
        terminal.feed(b"\x1b[31m" + text.encode() + b"x")
        frame = terminal.snapshot()
        cells = frame.row_patches[0].cells
        assert cells[0].text == text
        assert cells[1].text == "x"
        assert cells[0].style_id == cells[1].style_id
        assert terminal._render._utf8.cap >= len(text.encode())


@pytest.mark.parametrize("fragmented", [False, True])
def test_grapheme_suffix_limit_preserves_following_cell(fragmented: bool) -> None:
    with Terminal(10, 2) as terminal:
        data = b"\x1b[31me" + ("\u0301" * 180).encode() + b"x"
        if fragmented:
            for byte in data:
                terminal.feed(bytes([byte]))
        else:
            terminal.feed(data)
        cells = terminal.snapshot().row_patches[0].cells
        assert cells[0].text == "e" + "\u0301" * 64
        assert cells[1].text == "x"
        assert cells[0].style_id == cells[1].style_id
