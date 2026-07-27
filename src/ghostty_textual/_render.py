"""Private render-state extraction using only libghostty's public accessors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ghostty_textual._native import GhosttyError, Native
from ghostty_textual.cells import Cell, CellStyle, LinkInterner, Rgb, StyleInterner


@dataclass(frozen=True, slots=True)
class RowPatch:
    y: int
    cells: tuple[Cell, ...]
    wrapped: bool = False


@dataclass(frozen=True, slots=True)
class CursorState:
    x: int = 0
    y: int = 0
    visible: bool = False
    style: int = 1
    blinking: bool = False
    wide_tail: bool = False


@dataclass(frozen=True, slots=True)
class ViewportState:
    at_bottom: bool
    offset: int
    scrollback_rows: int
    total_rows: int


def _rgb(value: Any) -> Rgb:
    return (int(value.r), int(value.g), int(value.b))


class RenderState:
    """Owns reusable native render iterators and copies their contents to Python."""

    def __init__(self, native: Native) -> None:
        self.native = native
        ffi, lib = native.ffi, native.lib
        state = ffi.new("GhosttyRenderState*")
        rows = ffi.new("GhosttyRenderStateRowIterator*")
        cells = ffi.new("GhosttyRenderStateRowCells*")
        native.check(lib.ghostty_render_state_new(ffi.NULL, state), "render_state_new")
        try:
            native.check(
                lib.ghostty_render_state_row_iterator_new(ffi.NULL, rows),
                "render_state_row_iterator_new",
            )
            native.check(
                lib.ghostty_render_state_row_cells_new(ffi.NULL, cells),
                "render_state_row_cells_new",
            )
        except Exception:
            if rows[0] != ffi.NULL:
                lib.ghostty_render_state_row_iterator_free(rows[0])
            lib.ghostty_render_state_free(state[0])
            raise
        self._state = state[0]
        self._rows_ptr = rows
        self._rows = rows[0]
        self._cells_ptr = cells
        self._cells = cells[0]
        self._terminal: Any = None
        self._colors = ffi.new("GhosttyRenderStateColors*")
        self._colors.size = ffi.sizeof("GhosttyRenderStateColors")
        self._raw = ffi.new("GhosttyCell*")
        self._has_style = ffi.new("bool*")
        self._utf8_data = ffi.new("uint8_t[]", 256)
        self._utf8 = ffi.new("GhosttyBuffer*", {"ptr": self._utf8_data, "cap": 256, "len": 0})
        self._cell_row_keys = ffi.new(
            "GhosttyRenderStateRowCellsData[]",
            [
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_RAW,
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_HAS_STYLING,
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_GRAPHEMES_UTF8,
            ],
        )
        self._cell_row_values = ffi.new("void*[]", [self._raw, self._has_style, self._utf8])
        self._wide = ffi.new("int*")
        self._has_link = ffi.new("bool*")
        self._cell_keys = ffi.new(
            "GhosttyCellData[]",
            [lib.GHOSTTY_CELL_DATA_WIDE, lib.GHOSTTY_CELL_DATA_HAS_HYPERLINK],
        )
        self._cell_values = ffi.new("void*[]", [self._wide, self._has_link])
        self._style = ffi.new("GhosttyStyle*")
        self._style.size = ffi.sizeof("GhosttyStyle")
        self._fg = ffi.new("GhosttyColorRgb*")
        self._bg = ffi.new("GhosttyColorRgb*")
        self._style_keys = ffi.new(
            "GhosttyRenderStateRowCellsData[]",
            [
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_STYLE,
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_FG_COLOR,
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_BG_COLOR,
            ],
        )
        self._style_values = ffi.new("void*[]", [self._style, self._fg, self._bg])
        self._written = ffi.new("size_t*")
        self._default_style = CellStyle()
        self.closed = False

    def update(self, terminal: Any) -> None:
        self._assert_open()
        self._terminal = terminal
        self.native.check(
            self.native.lib.ghostty_render_state_update(self._state, terminal),
            "render_state_update",
        )
        self.native.check(
            self.native.lib.ghostty_render_state_colors_get(self._state, self._colors),
            "render_state_colors_get",
        )
        self._default_style = CellStyle(
            fg=_rgb(self._colors.foreground), bg=_rgb(self._colors.background)
        )

    def is_dirty(self) -> bool:
        lib = self.native.lib
        dirty = self.native.get_enum(
            lib.ghostty_render_state_get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_DIRTY,
            "render_state_get DIRTY",
        )
        return dirty != lib.GHOSTTY_RENDER_STATE_DIRTY_FALSE

    def read_rows(
        self,
        styles: StyleInterner,
        links: LinkInterner,
        *,
        force: bool,
    ) -> tuple[RowPatch, ...]:
        self._assert_open()
        lib = self.native.lib
        self.native.check(
            lib.ghostty_render_state_get(
                self._state, lib.GHOSTTY_RENDER_STATE_DATA_ROW_ITERATOR, self._rows_ptr
            ),
            "render_state_get ROW_ITERATOR",
        )
        patches: list[RowPatch] = []
        y = 0
        while lib.ghostty_render_state_row_iterator_next(self._rows):
            dirty = self.native.get_bool(
                lib.ghostty_render_state_row_get,
                self._rows,
                lib.GHOSTTY_RENDER_STATE_ROW_DATA_DIRTY,
                "render_state_row_get DIRTY",
            )
            if force or dirty:
                patches.append(
                    RowPatch(
                        y,
                        self._read_row(y, styles, links),
                        wrapped=self._read_row_wrapped(),
                    )
                )
            y += 1
        self._clear_row_dirty_flags()
        return tuple(patches)

    def _read_row_wrapped(self) -> bool:
        ffi, lib = self.native.ffi, self.native.lib
        row = ffi.new("GhosttyRow*")
        self.native.check(
            lib.ghostty_render_state_row_get(
                self._rows, lib.GHOSTTY_RENDER_STATE_ROW_DATA_RAW, row
            ),
            "render_state_row_get RAW",
        )
        return self.native.get_bool(
            lib.ghostty_row_get,
            row[0],
            lib.GHOSTTY_ROW_DATA_WRAP,
            "row_get WRAP",
        )

    def _clear_row_dirty_flags(self) -> None:
        ffi, lib = self.native.ffi, self.native.lib
        self.native.check(
            lib.ghostty_render_state_get(
                self._state,
                lib.GHOSTTY_RENDER_STATE_DATA_ROW_ITERATOR,
                self._rows_ptr,
            ),
            "render_state_get ROW_ITERATOR",
        )
        clean = ffi.new("bool*", False)
        while lib.ghostty_render_state_row_iterator_next(self._rows):
            self.native.check(
                lib.ghostty_render_state_row_set(
                    self._rows,
                    lib.GHOSTTY_RENDER_STATE_ROW_OPTION_DIRTY,
                    clean,
                ),
                "render_state_row_set DIRTY",
            )

    def _read_row(self, y: int, styles: StyleInterner, links: LinkInterner) -> tuple[Cell, ...]:
        ffi, lib = self.native.ffi, self.native.lib
        self.native.check(
            lib.ghostty_render_state_row_get(
                self._rows, lib.GHOSTTY_RENDER_STATE_ROW_DATA_CELLS, self._cells_ptr
            ),
            "render_state_row_get CELLS",
        )
        result: list[Cell] = []
        x = 0
        while lib.ghostty_render_state_row_cells_next(self._cells):
            self._utf8.len = 0
            rc = lib.ghostty_render_state_row_cells_get_multi(
                self._cells,
                3,
                self._cell_row_keys,
                self._cell_row_values,
                self._written,
            )
            if rc == -3:
                self._grow_utf8_buffer(int(self._utf8.len))
                self.native.check(
                    lib.ghostty_render_state_row_cells_get_multi(
                        self._cells,
                        3,
                        self._cell_row_keys,
                        self._cell_row_values,
                        self._written,
                    ),
                    "render_state_row_cells_get_multi",
                )
            else:
                self.native.check(rc, "render_state_row_cells_get_multi")
            self.native.check(
                lib.ghostty_cell_get_multi(
                    self._raw[0],
                    2,
                    self._cell_keys,
                    self._cell_values,
                    self._written,
                ),
                "cell_get_multi",
            )
            width_value = int(self._wide[0])
            if width_value == lib.GHOSTTY_CELL_WIDE_SPACER_TAIL:
                width = 0
            elif width_value == lib.GHOSTTY_CELL_WIDE_SPACER_HEAD:
                width = 1
            elif width_value == lib.GHOSTTY_CELL_WIDE_WIDE:
                width = 2
            else:
                width = 1
            text = bytes(ffi.buffer(self._utf8.ptr, int(self._utf8.len))).decode("utf-8", "replace")
            style_id = styles.intern(
                self._read_style() if self._has_style[0] else self._default_style
            )
            link_id = self._read_link(x, y, links) if self._has_link[0] else None
            result.append(Cell(text=text, width=width, style_id=style_id, link_id=link_id))
            x += 1
        return tuple(result)

    def _grow_utf8_buffer(self, needed: int) -> None:
        ffi = self.native.ffi
        capacity = max(needed, int(self._utf8.cap) * 2)
        self._utf8_data = ffi.new("uint8_t[]", capacity)
        self._utf8.ptr = self._utf8_data
        self._utf8.cap = capacity
        self._utf8.len = 0

    def _read_style(self) -> CellStyle:
        lib = self.native.lib
        self._style.size = self.native.ffi.sizeof("GhosttyStyle")
        self.native.check(
            lib.ghostty_render_state_row_cells_get(
                self._cells,
                lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_STYLE,
                self._style,
            ),
            "render_state_row_cells_get STYLE",
        )
        fg_rc = lib.ghostty_render_state_row_cells_get(
            self._cells,
            lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_FG_COLOR,
            self._fg,
        )
        bg_rc = lib.ghostty_render_state_row_cells_get(
            self._cells,
            lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_BG_COLOR,
            self._bg,
        )
        if fg_rc:
            self._fg[0] = self._colors.foreground
        if bg_rc:
            self._bg[0] = self._colors.background
        style = self._style
        underline_color = None
        if style.underline_color.tag == lib.GHOSTTY_STYLE_COLOR_RGB:
            underline_color = _rgb(style.underline_color.value.rgb)
        elif style.underline_color.tag == lib.GHOSTTY_STYLE_COLOR_PALETTE:
            underline_color = _rgb(self._colors.palette[int(style.underline_color.value.palette)])
        return CellStyle(
            fg=_rgb(self._fg),
            bg=_rgb(self._bg),
            underline_color=underline_color,
            bold=bool(style.bold),
            faint=bool(style.faint),
            italic=bool(style.italic),
            blink=bool(style.blink),
            inverse=bool(style.inverse),
            invisible=bool(style.invisible),
            strikethrough=bool(style.strikethrough),
            overline=bool(style.overline),
            underline=int(style.underline),
        )

    def _read_link(self, x: int, y: int, links: LinkInterner) -> int | None:
        ffi, lib = self.native.ffi, self.native.lib
        ref = self.native.grid_ref(self._terminal, x, y)
        needed = ffi.new("size_t*")
        rc = lib.ghostty_grid_ref_hyperlink_uri(ref, ffi.NULL, 0, needed)
        if rc not in (0, -3) or needed[0] == 0:
            return None
        buf = ffi.new("uint8_t[]", int(needed[0]))
        self.native.check(
            lib.ghostty_grid_ref_hyperlink_uri(ref, buf, int(needed[0]), needed),
            "grid_ref_hyperlink_uri",
        )
        return links.intern(bytes(ffi.buffer(buf, int(needed[0]))).decode("utf-8", "replace"))

    def read_cursor(self) -> CursorState:
        lib = self.native.lib
        get = lib.ghostty_render_state_get
        present = self.native.get_bool(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VIEWPORT_HAS_VALUE,
            "render_state_get CURSOR_VIEWPORT_HAS_VALUE",
        )
        visible = self.native.get_bool(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VISIBLE,
            "render_state_get CURSOR_VISIBLE",
        )
        style = self.native.get_enum(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VISUAL_STYLE,
            "render_state_get CURSOR_VISUAL_STYLE",
        )
        blinking = self.native.get_bool(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_BLINKING,
            "render_state_get CURSOR_BLINKING",
        )
        if not present:
            return CursorState(visible=False, style=style, blinking=blinking)
        x = self.native.get_u16(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VIEWPORT_X,
            "render_state_get CURSOR_VIEWPORT_X",
        )
        y = self.native.get_u16(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VIEWPORT_Y,
            "render_state_get CURSOR_VIEWPORT_Y",
        )
        wide_tail = self.native.get_bool(
            get,
            self._state,
            lib.GHOSTTY_RENDER_STATE_DATA_CURSOR_VIEWPORT_WIDE_TAIL,
            "render_state_get CURSOR_VIEWPORT_WIDE_TAIL",
        )
        return CursorState(x, y, visible, style, blinking, wide_tail)

    def color_signature(self) -> tuple[tuple[int, int, int], ...]:
        """Return resolved defaults and palette for non-dirty colour invalidation."""
        return (
            _rgb(self._colors.background),
            _rgb(self._colors.foreground),
            _rgb(self._colors.cursor),
            *(_rgb(color) for color in self._colors.palette),
        )

    def clear_global_dirty(self) -> None:
        clean = self.native.ffi.new("int*", self.native.lib.GHOSTTY_RENDER_STATE_DIRTY_FALSE)
        self.native.check(
            self.native.lib.ghostty_render_state_set(
                self._state, self.native.lib.GHOSTTY_RENDER_STATE_OPTION_DIRTY, clean
            ),
            "render_state_set DIRTY",
        )

    def close(self) -> None:
        if self.closed:
            return
        lib = self.native.lib
        lib.ghostty_render_state_row_cells_free(self._cells)
        lib.ghostty_render_state_row_iterator_free(self._rows)
        lib.ghostty_render_state_free(self._state)
        self.closed = True

    def _assert_open(self) -> None:
        if self.closed:
            raise GhosttyError("render state is closed")
