# ghostty-textual Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Textual terminal widget over `libghostty-vt` that never owns a process, so applications that already manage their own PTY lifecycle can embed a modern terminal.

**Architecture:** Two layers with one native boundary. The private `_native.py`/`_render.py` modules are the only code that touches C; `emulator.py` exposes a headless `Terminal` (bytes in, effects out, frames on demand); `widget.py` renders frames into Textual strips and encodes input. `libghostty-vt`'s C API is explicitly unstable, so all churn is confined below `emulator.py`.

**Tech Stack:** Python 3.12+, Textual 8.2.8, `pyghostty==0.1.0` (CFFI ABI mode over a bundled `libghostty-vt` shared library), pytest, ruff, hatchling.

**Spec:** [`docs/superpowers/specs/2026-07-25-ghostty-textual-design-v2.md`](../specs/2026-07-25-ghostty-textual-design-v2.md)

## Scope

This plan covers spec §12 steps **3–7**: the library, from frame extraction through security policy. Steps 1–2 (native boundary, effects) are **already implemented and committed** — `src/ghostty_textual/_native.py` plus `tests/test_abi.py`, `test_effects.py`, `test_reset.py`, `test_resize.py`, 31 tests passing.

Step 8, the `pysshmanager` cutover, is deliberately **excluded**. It lives in a different repository, and the library must be independently green before anything migrates onto it. It gets its own plan once Task 12 lands.

## Global Constraints

- Python `>=3.12`. Textual `>=8.2.8,<9`. `pyghostty==0.1.0` — pinned exactly, never widened without CI proof, because we bind its **private** `_ffi`/`_cdef` modules.
- **The library never spawns a process, opens a PTY, or owns a transport.** Bytes in, bytes out.
- `emulator.py`, `cells.py`, `theme.py`, `keys.py` must not import `textual`. `widget.py` must not import `pyghostty` or `cffi`.
- `libghostty-vt` is **not thread-safe**. Every `Terminal` operation happens on the owning thread/event loop.
- Errors from the binding are **fatal and typed** (`GhosttyError`), never logged-and-swallowed. Malformed *remote* bytes are not errors — libghostty handles them.
- No `__del__` for correctness. Explicit `close()` and context managers only.
- CFFI callback objects are held on the owning Python object until after the C object is freed.
- All public dataclasses are `frozen=True, slots=True`. No public object may hold a C grid reference past the next terminal mutation.
- Line length 100. `ruff check src tests` must pass. Every task ends green.

## Verified C API (use these exact names)

```
ghostty_render_state_new(allocator, GhosttyRenderState* out)      -> GhosttyResult
ghostty_render_state_update(state, terminal)                      -> GhosttyResult
ghostty_render_state_begin_update(state, terminal)                -> GhosttyResult
ghostty_render_state_end_update(state)                            -> GhosttyResult
ghostty_render_state_get(state, GhosttyRenderStateData, void* out)-> GhosttyResult
ghostty_render_state_colors_get(state, GhosttyRenderStateColors*) -> GhosttyResult
ghostty_render_state_row_iterator_new(allocator, out_iterator)    -> GhosttyResult
ghostty_render_state_row_iterator_next(iterator)                  -> bool
ghostty_render_state_row_get(iterator, GhosttyRenderStateRowData, void* out)
ghostty_render_state_row_set(iterator, GhosttyRenderStateRowOption, const void*)
ghostty_render_state_row_cells_new(allocator, out_cells)          -> GhosttyResult
ghostty_render_state_row_cells_next(cells)                        -> bool
ghostty_render_state_row_cells_get(cells, GhosttyRenderStateRowCellsData, void* out)
```

An iterator is **bound to a state** by `ghostty_render_state_get(state, GHOSTTY_RENDER_STATE_DATA_ROW_ITERATOR, iterator)`. A cells iterator is bound to a row by `ghostty_render_state_row_get(iterator, GHOSTTY_RENDER_STATE_ROW_DATA_CELLS, cells)`.

Enum values confirmed at runtime: `ROW_DATA_INVALID=0`, `ROW_DATA_DIRTY=1`, `ROW_DATA_RAW=2`, `ROW_DATA_CELLS=3`, `ROW_DATA_SELECTION=4`.

Cell data keys: `RAW`, `STYLE`, `GRAPHEMES_LEN`, `GRAPHEMES_BUF`, `GRAPHEMES_UTF8`, `BG_COLOR`, `FG_COLOR`, `SELECTED`, `HAS_STYLING`.

Cursor comes from **viewport** fields: `CURSOR_VIEWPORT_X`, `CURSOR_VIEWPORT_Y`, `CURSOR_VIEWPORT_HAS_VALUE`, `CURSOR_VIEWPORT_WIDE_TAIL`, `CURSOR_VISIBLE`, `CURSOR_VISUAL_STYLE`, `CURSOR_BLINKING`.

## File Structure

| File | Responsibility | Imports C? |
|---|---|---|
| `_native.py` | Loader, ABI verification, union twins | yes (**done**) |
| `_render.py` | Render state → `RowPatch`/`CursorState`; dirty acknowledgement | yes |
| `cells.py` | `Cell`, `CellStyle`, bounded generation-scoped interning | no |
| `theme.py` | `TerminalTheme`, palette → Ghostty colour options | no |
| `emulator.py` | `Terminal`: lifecycle, `feed`, `snapshot`, viewport, encoders | yes |
| `keys.py` | Textual `events.Key` → `KeyEvent` | no |
| `widget.py` | `TerminalView`: shadow buffer, strips, input, selection | no |

`_render.py` joining the native boundary is a **deliberate refinement** of spec §1, which named only `emulator.py`. The one-boundary principle is preserved — the boundary is the private `_`-prefixed modules — but render-state iteration is a big enough job to deserve its own file rather than doubling `emulator.py`'s size.

---

### Task 1: Cell model and bounded interning

**Files:**
- Create: `src/ghostty_textual/cells.py`
- Test: `tests/test_cells.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Cell(text: str, width: Literal[0,1,2], style_id: int, link_id: int | None)`; `CellStyle` (frozen); `StyleInterner` with `.generation: int`, `.intern(style: CellStyle) -> int`, `.resolve(style_id: int) -> CellStyle`, `.rollover_pending: bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cells.py
import pytest
from ghostty_textual.cells import Cell, CellStyle, StyleInterner

RED = CellStyle(fg=(255, 0, 0), bg=(0, 0, 0))
BLUE = CellStyle(fg=(0, 0, 255), bg=(0, 0, 0))


def test_identical_styles_intern_to_the_same_id():
    interner = StyleInterner(limit=16)
    assert interner.intern(RED) == interner.intern(CellStyle(fg=(255, 0, 0), bg=(0, 0, 0)))


def test_different_styles_get_different_ids():
    interner = StyleInterner(limit=16)
    assert interner.intern(RED) != interner.intern(BLUE)


def test_resolve_returns_the_original_style():
    interner = StyleInterner(limit=16)
    assert interner.resolve(interner.intern(RED)) == RED


def test_exceeding_the_limit_flags_a_rollover():
    """A hostile remote can emit unbounded true-colour combinations."""
    interner = StyleInterner(limit=4)
    for value in range(4):
        interner.intern(CellStyle(fg=(value, 0, 0), bg=None))
    assert not interner.rollover_pending
    interner.intern(CellStyle(fg=(99, 0, 0), bg=None))
    assert interner.rollover_pending


def test_rollover_increments_generation_and_empties_the_table():
    interner = StyleInterner(limit=4)
    first_generation = interner.generation
    for value in range(5):
        interner.intern(CellStyle(fg=(value, 0, 0), bg=None))
    interner.rollover()
    assert interner.generation == first_generation + 1
    assert not interner.rollover_pending
    with pytest.raises(KeyError):
        interner.resolve(0)


def test_continuation_cell_has_zero_width():
    assert Cell(text="", width=0, style_id=0, link_id=None).width == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cells.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual.cells'`

- [ ] **Step 3: Implement `cells.py`**

```python
"""Cell model and bounded style interning. No C, no Textual."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Rgb = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class CellStyle:
    fg: Rgb | None = None
    bg: Rgb | None = None
    underline_color: Rgb | None = None
    bold: bool = False
    faint: bool = False
    italic: bool = False
    blink: bool = False
    inverse: bool = False
    invisible: bool = False
    strikethrough: bool = False
    overline: bool = False
    underline: int = 0  # 0 = none


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    width: Literal[0, 1, 2]  # 0 = continuation of a wide grapheme
    style_id: int
    link_id: int | None = None


DEFAULT_STYLE = CellStyle()


@dataclass(slots=True)
class StyleInterner:
    """Maps styles to small ints, bounded so a hostile remote cannot exhaust memory.

    Resolved RGB values are part of the key, so an OSC palette change naturally
    produces new ids rather than needing separate invalidation.
    """

    limit: int = 4096
    generation: int = 0
    rollover_pending: bool = False
    _forward: dict[CellStyle, int] = field(default_factory=dict)
    _reverse: list[CellStyle] = field(default_factory=list)

    def intern(self, style: CellStyle) -> int:
        existing = self._forward.get(style)
        if existing is not None:
            return existing
        if len(self._reverse) >= self.limit:
            self.rollover_pending = True
        style_id = len(self._reverse)
        self._forward[style] = style_id
        self._reverse.append(style)
        return style_id

    def resolve(self, style_id: int) -> CellStyle:
        try:
            return self._reverse[style_id]
        except IndexError as exc:
            raise KeyError(style_id) from exc

    def rollover(self) -> None:
        """Discard the table and bump the generation.

        Every style_id already in a shadow buffer is invalidated by this, so the
        caller MUST force a full frame and swap the shadow buffer and tables
        together. See spec v2 §4.
        """
        self._forward.clear()
        self._reverse.clear()
        self.generation += 1
        self.rollover_pending = False
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cells.py -v`
Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/cells.py tests/test_cells.py
git commit -m "feat: cell model with bounded generation-scoped style interning"
```

---

### Task 2: Render state wrapper

**Files:**
- Create: `src/ghostty_textual/_render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Native` and `GhosttyError` from `_native`; `Cell`, `CellStyle`, `StyleInterner` from `cells`.
- Produces: `RowPatch(y: int, cells: tuple[Cell, ...])`; `CursorState(x: int, y: int, visible: bool, style: int, blinking: bool, wide_tail: bool)`; `RenderState` with `.update(terminal) -> None`, `.is_dirty() -> bool`, `.read_rows(interner, *, force: bool) -> tuple[RowPatch, ...]`, `.read_cursor() -> CursorState`, `.close() -> None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render.py
from ghostty_textual._render import RenderState
from ghostty_textual.cells import StyleInterner
from tests.conftest import make_terminal


def test_reading_rows_returns_the_fed_text(native):
    harness = make_terminal(native, cols=20, rows=3)
    state = RenderState(native)
    try:
        harness.feed(b"hello")
        state.update(harness.terminal)
        rows = state.read_rows(StyleInterner(), force=True)
        assert "".join(cell.text for cell in rows[0].cells).rstrip() == "hello"
    finally:
        state.close()
        harness.close()


def test_only_dirty_rows_are_returned(native):
    harness = make_terminal(native, cols=20, rows=5)
    state = RenderState(native)
    interner = StyleInterner()
    try:
        harness.feed(b"one\r\ntwo\r\nthree")
        state.update(harness.terminal)
        state.read_rows(interner, force=True)  # commits and clears dirty flags

        harness.feed(b"\x1b[1;1Hx")  # touch row 0 only
        state.update(harness.terminal)
        patched = state.read_rows(interner, force=False)
        assert [patch.y for patch in patched] == [0]
    finally:
        state.close()
        harness.close()


def test_clean_state_reports_no_dirty_rows(native):
    harness = make_terminal(native, cols=10, rows=2)
    state = RenderState(native)
    interner = StyleInterner()
    try:
        harness.feed(b"hi")
        state.update(harness.terminal)
        state.read_rows(interner, force=True)
        state.update(harness.terminal)
        assert state.read_rows(interner, force=False) == ()
    finally:
        state.close()
        harness.close()


def test_cursor_comes_from_viewport_coordinates(native):
    harness = make_terminal(native, cols=20, rows=3)
    state = RenderState(native)
    try:
        harness.feed(b"abc")
        state.update(harness.terminal)
        cursor = state.read_cursor()
        assert (cursor.x, cursor.y) == (3, 0)
        assert cursor.visible
    finally:
        state.close()
        harness.close()


def test_styles_are_interned_not_duplicated(native):
    harness = make_terminal(native, cols=20, rows=1)
    state = RenderState(native)
    interner = StyleInterner()
    try:
        harness.feed(b"\x1b[31maaa\x1b[0m")
        state.update(harness.terminal)
        rows = state.read_rows(interner, force=True)
        red_ids = {cell.style_id for cell in rows[0].cells[:3]}
        assert len(red_ids) == 1
    finally:
        state.close()
        harness.close()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual._render'`

- [ ] **Step 3: Spike the cell encoding (30 minutes, throwaway script)**

The cell payload is a packed `uint32` and its layout is not in the header. Do not
guess it — determine it, then write the decoder. Already established:

| Fact | Evidence |
|---|---|
| Codepoint is `raw >> 2` | `0x184 >> 2 == 0x61 == 'a'`, `0x188 >> 2 == 0x62 == 'b'` |
| Styled cells set a high bit | `'a'` red → `0x4000184`; unstyled `'c'` → `0x18c` |
| `GRAPHEMES_UTF8` returns `rc=-3` for single-codepoint cells | only populated for multi-codepoint graphemes |
| `HAS_STYLING` is a cheap early-out | `True` only for the SGR-styled cells |
| **Row-level `ROW_DATA_RAW` returns a pointer** | `rc=0`, a bulk `uint32_t*` array for the whole row |

Write a scratch script that feeds known input and prints `raw` in binary for each
cell, and answer exactly these:

1. What do the low 2 bits mean? (Candidates: cell width 0/1/2, or a content tag.)
2. Which bit is "has style", and is there a style **index** in the high bits?
3. How is a wide-character continuation cell encoded — zero `raw`, or a width tag?
4. Does the row-level `RAW` pointer give `cols` entries of the same `uint32`? If
   so, **use it**: one FFI call per row instead of one per cell, with per-cell
   `row_cells_get` only for cells where `HAS_STYLING` or the grapheme bit is set.

Suggested input: `b"a\x1b[31mb\x1b[0m"` + `"界".encode()` + `b"\xcc\x81"` (combining acute),
in a 10x1 terminal, printing `f"{raw:032b}"` per cell.

Record the answers as a comment block at the top of `_render.py`. This is the
only reverse-engineered contract in the library; make it legible for the next
person, and pin it with the tests in this task so a libghostty upgrade that
changes the packing fails loudly.

- [ ] **Step 4: Implement `_render.py`**

Key points the implementer must get right:
- Allocate the row iterator and cells iterator **once** and reuse them; binding is `render_state_get(state, DATA_ROW_ITERATOR, iterator)` and `render_state_row_get(iterator, ROW_DATA_CELLS, cells)`.
- After committing a frame, clear **both** the per-row dirty flag (`row_set(iterator, ROW_OPTION_DIRTY, false)`) and re-check the global `DATA_DIRTY`. Clearing one does not clear the other.
- Grapheme text comes from `ROW_CELLS_DATA_GRAPHEMES_UTF8`; width 0 marks a continuation cell.

```python
"""Render state → immutable frames. Part of the native boundary."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ghostty_textual._native import Native
from ghostty_textual.cells import Cell, CellStyle, StyleInterner


@dataclass(frozen=True, slots=True)
class RowPatch:
    y: int
    cells: tuple[Cell, ...]


@dataclass(frozen=True, slots=True)
class CursorState:
    x: int
    y: int
    visible: bool
    style: int
    blinking: bool
    wide_tail: bool


class RenderState:
    def __init__(self, native: Native) -> None:
        self._native = native
        ffi, lib = native.ffi, native.lib
        self._state = ffi.new("GhosttyRenderState*")
        native.check(lib.ghostty_render_state_new(ffi.NULL, self._state), "render_state_new")
        self._iterator = ffi.new("GhosttyRenderStateRowIterator*")
        native.check(
            lib.ghostty_render_state_row_iterator_new(ffi.NULL, self._iterator),
            "row_iterator_new",
        )
        self._cells = ffi.new("GhosttyRenderStateRowCells*")
        native.check(
            lib.ghostty_render_state_row_cells_new(ffi.NULL, self._cells), "row_cells_new"
        )

    def update(self, terminal: Any) -> None:
        lib = self._native.lib
        self._native.check(
            lib.ghostty_render_state_update(self._state[0], terminal), "render_state_update"
        )

    def is_dirty(self) -> bool:
        ffi, lib = self._native.ffi, self._native.lib
        out = ffi.new("bool*")
        self._native.check(
            lib.ghostty_render_state_get(
                self._state[0], lib.GHOSTTY_RENDER_STATE_DATA_DIRTY, out
            ),
            "get DIRTY",
        )
        return bool(out[0])

    def read_rows(self, interner: StyleInterner, *, force: bool) -> tuple[RowPatch, ...]:
        """Copy dirty rows (or all rows when `force`) and clear their dirty flags."""
        ffi, lib = self._native.ffi, self._native.lib
        self._native.check(
            lib.ghostty_render_state_get(
                self._state[0], lib.GHOSTTY_RENDER_STATE_DATA_ROW_ITERATOR, self._iterator
            ),
            "bind row iterator",
        )
        patches: list[RowPatch] = []
        y = 0
        clear = ffi.new("bool*", False)
        dirty = ffi.new("bool*")
        while lib.ghostty_render_state_row_iterator_next(self._iterator[0]):
            self._native.check(
                lib.ghostty_render_state_row_get(
                    self._iterator[0], lib.GHOSTTY_RENDER_STATE_ROW_DATA_DIRTY, dirty
                ),
                "row DIRTY",
            )
            if force or dirty[0]:
                patches.append(RowPatch(y=y, cells=self._read_row_cells(interner)))
                lib.ghostty_render_state_row_set(
                    self._iterator[0], lib.GHOSTTY_RENDER_STATE_ROW_OPTION_DIRTY, clear
                )
            y += 1
        return tuple(patches)

    def _read_row_cells(self, interner: StyleInterner) -> tuple[Cell, ...]:
        ffi, lib = self._native.ffi, self._native.lib
        self._native.check(
            lib.ghostty_render_state_row_get(
                self._iterator[0], lib.GHOSTTY_RENDER_STATE_ROW_DATA_CELLS, self._cells
            ),
            "row CELLS",
        )
        cells: list[Cell] = []
        while lib.ghostty_render_state_row_cells_next(self._cells[0]):
            text = self._cell_text()
            style = self._cell_style()
            width = 0 if text == "" and cells and cells[-1].width == 2 else (2 if len(text.encode()) > 1 and self._is_wide(text) else 1)
            cells.append(Cell(text=text, width=width, style_id=interner.intern(style)))
        return tuple(cells)

    def read_cursor(self) -> CursorState:
        ffi, lib = self._native.ffi, self._native.lib

        def flag(name: str) -> bool:
            out = ffi.new("bool*")
            self._native.check(
                lib.ghostty_render_state_get(
                    self._state[0], getattr(lib, f"GHOSTTY_RENDER_STATE_DATA_{name}"), out
                ),
                f"get {name}",
            )
            return bool(out[0])

        def number(name: str) -> int:
            out = ffi.new("uint16_t*")
            self._native.check(
                lib.ghostty_render_state_get(
                    self._state[0], getattr(lib, f"GHOSTTY_RENDER_STATE_DATA_{name}"), out
                ),
                f"get {name}",
            )
            return int(out[0])

        return CursorState(
            x=number("CURSOR_VIEWPORT_X"),
            y=number("CURSOR_VIEWPORT_Y"),
            visible=flag("CURSOR_VISIBLE") and flag("CURSOR_VIEWPORT_HAS_VALUE"),
            style=number("CURSOR_VISUAL_STYLE"),
            blinking=flag("CURSOR_BLINKING"),
            wide_tail=flag("CURSOR_VIEWPORT_WIDE_TAIL"),
        )

    def close(self) -> None:
        lib = self._native.lib
        for handle, free in (
            (self._cells, lib.ghostty_render_state_row_cells_free),
            (self._iterator, lib.ghostty_render_state_row_iterator_free),
            (self._state, lib.ghostty_render_state_free),
        ):
            if handle is not None:
                free(handle[0])
        self._cells = self._iterator = self._state = None
```

`_cell_text`, `_cell_style`, and the width determination follow directly from the
Step 3 spike. Colours come from `ROW_CELLS_DATA_FG_COLOR`/`BG_COLOR` as
`GhosttyColorRgb` (fields `r`, `g`, `b`, all `uint8_t`) — these are *resolved*
values, which is exactly what the interner wants as its key, so a palette change
produces new ids without separate invalidation. Attribute flags come from
`ROW_CELLS_DATA_STYLE`. Skip both when `HAS_STYLING` is false and use
`DEFAULT_STYLE`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_render.py -v`
Expected: 5 passed

- [ ] **Step 6: Add a wide-character test**

```python
def test_wide_character_produces_a_continuation_cell(native):
    harness = make_terminal(native, cols=10, rows=1)
    state = RenderState(native)
    try:
        harness.feed("界".encode())
        state.update(harness.terminal)
        cells = state.read_rows(StyleInterner(), force=True)[0].cells
        assert cells[0].text == "界" and cells[0].width == 2
        assert cells[1].width == 0
    finally:
        state.close()
        harness.close()
```

Run: `uv run pytest tests/test_render.py -v`
Expected: 6 passed

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/_render.py tests/test_render.py
git commit -m "feat: render state wrapper with dirty-row extraction"
```

---

### Task 3: Terminal lifecycle, feed, and effects

**Files:**
- Create: `src/ghostty_textual/emulator.py`
- Test: `tests/test_emulator_lifecycle.py`

**Interfaces:**
- Consumes: `Native`, `GhosttyError`, `load` from `_native`; `RenderState` from `_render`.
- Produces: `Terminal(cols, rows, *, scrollback=5000, theme=None, clipboard=ClipboardPolicy(), limits=ResourceLimits())`; `Terminal.feed(bytes) -> TerminalEffects`; `.close()`; `.closed`; context manager. `TerminalEffects(pty_writes: tuple[bytes, ...], notifications: tuple[TerminalNotification, ...])`. `ClipboardPolicy`, `ResourceLimits`, `TitleChanged`, `BellRang`, `ClipboardWritten`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_emulator_lifecycle.py
import pytest
from ghostty_textual._native import GhosttyError
from ghostty_textual.emulator import BellRang, Terminal, TitleChanged


def test_feed_returns_query_replies_as_pty_writes():
    with Terminal(80, 24) as terminal:
        assert terminal.feed(b"\x1b[6n").pty_writes == (b"\x1b[1;1R",)


def test_pty_writes_preserve_order_within_one_feed():
    with Terminal(80, 24) as terminal:
        effects = terminal.feed(b"\x1b[6n\x1b[c")
        assert effects.pty_writes == (b"\x1b[1;1R", b"\x1b[?62;22c")


def test_title_change_is_reported_as_a_notification():
    with Terminal(80, 24) as terminal:
        effects = terminal.feed(b"\x1b]0;hello\x07")
        assert TitleChanged(title="hello") in effects.notifications
        assert terminal.title == "hello"


def test_bell_is_reported():
    with Terminal(80, 24) as terminal:
        assert any(isinstance(n, BellRang) for n in terminal.feed(b"\x07").notifications)


def test_clipboard_write_is_refused_by_default():
    """OSC 52 is an exfiltration vector; spec v2 §6.11 defaults it off."""
    with Terminal(80, 24) as terminal:
        effects = terminal.feed(b"\x1b]52;c;aGVsbG8=\x07")
        assert effects.notifications == ()


def test_close_is_idempotent():
    terminal = Terminal(80, 24)
    terminal.close()
    terminal.close()
    assert terminal.closed


def test_every_operation_after_close_raises():
    terminal = Terminal(80, 24)
    terminal.close()
    with pytest.raises(RuntimeError):
        terminal.feed(b"x")


def test_malformed_bytes_are_not_an_error():
    """libghostty handles untrusted input; only wrapper bugs raise."""
    with Terminal(80, 24) as terminal:
        terminal.feed(b"\x1b[?4m")          # the CSI that froze pyte
        terminal.feed(b"\xff\xfe\x00garbage")
        terminal.feed(b"\x1b[999999999J")


def test_utf8_split_across_feeds_is_reassembled():
    with Terminal(80, 24) as terminal:
        terminal.feed(b"\xe2\x94")
        terminal.feed(b"\x80")
        frame = terminal.snapshot(force=True)
        assert frame.row_patches[0].cells[0].text == "─"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_emulator_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual.emulator'`

- [ ] **Step 3: Implement `emulator.py` lifecycle and feed**

Requirements the tests do not fully express:
- The `WRITE_PTY` callback **only appends to a list**. It must not call `vt_write`, await, or block — it fires synchronously inside `ghostty_terminal_vt_write`.
- Exceptions raised inside any CFFI callback are captured and re-raised as `GhosttyError` **after** `vt_write` returns, never allowed to unwind through C.
- All callback objects live in `self._keepalive` until after `ghostty_terminal_free`.
- Record `threading.get_ident()` at construction; assert it in every public method under `if __debug__`.
- Register `OPT_SIZE` returning cached rows/columns with `cell_width = cell_height = 0` (spec §3.4).

```python
@dataclass(frozen=True, slots=True)
class TerminalEffects:
    pty_writes: tuple[bytes, ...] = ()
    notifications: tuple[TerminalNotification, ...] = ()


class Terminal:
    def feed(self, data: bytes) -> TerminalEffects:
        self._assert_usable()
        self._pending_writes.clear()
        self._pending_notifications.clear()
        self._callback_error = None
        self._lib.ghostty_terminal_vt_write(self._terminal, data, len(data))
        if self._callback_error is not None:
            raise GhosttyError("callback failed during feed") from self._callback_error
        return TerminalEffects(
            pty_writes=tuple(self._pending_writes),
            notifications=tuple(self._pending_notifications),
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_emulator_lifecycle.py -v`
Expected: 9 passed (the last one needs Task 4's `snapshot`; mark it `xfail` now and remove the marker in Task 4)

- [ ] **Step 5: Add leak-loop and thread-affinity tests**

```python
def test_create_feed_close_loop_does_not_leak_handles():
    for _ in range(200):
        with Terminal(80, 24) as terminal:
            terminal.feed(b"hello\r\n")


def test_use_from_another_thread_is_rejected():
    """libghostty-vt is not thread-safe; the confinement must be enforced."""
    import threading

    failures: list[BaseException] = []
    with Terminal(80, 24) as terminal:
        def other_thread() -> None:
            try:
                terminal.feed(b"x")
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
    assert failures and isinstance(failures[0], RuntimeError)
```

Run: `uv run pytest tests/test_emulator_lifecycle.py -v`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/emulator.py tests/test_emulator_lifecycle.py
git commit -m "feat: Terminal lifecycle, feed, and effect collection"
```

---

### Task 4: Snapshot and frames

**Files:**
- Modify: `src/ghostty_textual/emulator.py`
- Test: `tests/test_emulator_frames.py`

**Interfaces:**
- Consumes: `RenderState`, `RowPatch`, `CursorState`, `StyleInterner`.
- Produces: `Terminal.snapshot(*, force: bool = False) -> Frame | None`; `Frame(generation, cols, rows, full_redraw, row_patches, cursor, viewport, frame_pending)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_emulator_frames.py
from ghostty_textual.emulator import Terminal


def test_snapshot_after_output_returns_a_frame():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        frame = terminal.snapshot()
        assert frame is not None
        assert "".join(c.text for c in frame.row_patches[0].cells).rstrip() == "hello"


def test_snapshot_with_no_change_returns_none():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        assert terminal.snapshot() is None


def test_force_returns_a_full_frame_even_when_clean():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        frame = terminal.snapshot(force=True)
        assert frame is not None and frame.full_redraw
        assert len(frame.row_patches) == 3


def test_only_changed_rows_are_patched():
    with Terminal(20, 5) as terminal:
        terminal.feed(b"a\r\nb\r\nc")
        terminal.snapshot()
        terminal.feed(b"\x1b[1;1Hz")
        frame = terminal.snapshot()
        assert [p.y for p in frame.row_patches] == [0]


def test_resize_produces_a_frame_without_a_feed():
    """Spec v2 §3.1: mutations other than feed must still be describable."""
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        terminal.resize(40, 6)
        frame = terminal.snapshot()
        assert frame is not None and (frame.cols, frame.rows) == (40, 6)


def test_frame_carries_the_intern_generation():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hi")
        assert terminal.snapshot().generation == 0


def test_style_rollover_forces_a_full_frame():
    with Terminal(20, 3, limits=ResourceLimits(max_interned_styles=4)) as terminal:
        terminal.feed(b"hi")
        first = terminal.snapshot()
        for value in range(10):
            terminal.feed(f"\x1b[38;2;{value};0;0mx".encode())
        second = terminal.snapshot()
        assert second.generation > first.generation
        assert second.full_redraw
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_emulator_frames.py -v`
Expected: FAIL — `AttributeError: 'Terminal' object has no attribute 'snapshot'`

- [ ] **Step 3: Implement `snapshot()`**

Critical ordering, from spec §3.1 and §4:
1. `render_state.update(terminal)`.
2. If not `force` and not `is_dirty()` and no pending rollover → return `None`.
3. If `interner.rollover_pending` → `interner.rollover()`, set `force = True`.
4. Read rows (which clears per-row dirty flags), read cursor, read viewport.
5. Build the immutable `Frame`, stamping the current `interner.generation`.

Document on the method that dirty state is cleared before returning, so a caller that raises while applying a frame must recover with `snapshot(force=True)`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_emulator_frames.py -v`
Expected: 7 passed

- [ ] **Step 5: Remove the xfail from Task 3's UTF-8 test and run the whole suite**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/emulator.py tests/test_emulator_frames.py
git commit -m "feat: snapshot() with dirty-row frames and generation stamping"
```

---

### Task 5: Viewport and scrollback

**Files:**
- Modify: `src/ghostty_textual/emulator.py`
- Test: `tests/test_viewport.py`

**Interfaces:**
- Produces: `Terminal.scroll_viewport(request: ScrollRequest)`, `.scroll_to_top()`, `.scroll_to_bottom()`, `.viewport -> ViewportState`. `ScrollRequest` = `ScrollTop() | ScrollBottom() | ScrollDelta(n) | ScrollToRow(n)`. `ViewportState(at_bottom, offset, scrollback_rows, total_rows)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viewport.py
from ghostty_textual.emulator import ScrollDelta, Terminal


def _fill(terminal, count=40):
    terminal.feed(b"".join(b"line%d\r\n" % i for i in range(count)))


def test_fresh_terminal_is_at_bottom():
    with Terminal(20, 5) as terminal:
        assert terminal.viewport.at_bottom


def test_scrolling_to_top_leaves_the_bottom():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        assert not terminal.viewport.at_bottom


def test_scroll_to_bottom_resumes_following():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        terminal.scroll_to_bottom()
        assert terminal.viewport.at_bottom


def test_scrolled_content_stays_anchored_while_output_arrives():
    """The behaviour spec v1 got wrong. A real terminal does not drift."""
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        before = terminal.snapshot(force=True)
        before_text = [
            "".join(c.text for c in patch.cells).rstrip() for patch in before.row_patches
        ]
        terminal.feed(b"newline\r\n" * 3)
        after = terminal.snapshot(force=True)
        after_text = [
            "".join(c.text for c in patch.cells).rstrip() for patch in after.row_patches
        ]
        assert before_text == after_text


def test_scrollback_is_bounded_by_the_configured_limit():
    with Terminal(20, 5, scrollback=10) as terminal:
        _fill(terminal, count=200)
        assert terminal.viewport.scrollback_rows <= 10


def test_scroll_delta_moves_by_the_requested_rows():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_viewport(ScrollDelta(-3))
        assert not terminal.viewport.at_bottom
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_viewport.py -v`
Expected: FAIL — `ImportError: cannot import name 'ScrollDelta'`

- [ ] **Step 3: Implement viewport**

Use `Native.scroll_viewport(terminal, tag, value)` — the struct twin from `_native.py`, already verified working. Tags: `GHOSTTY_SCROLL_VIEWPORT_TOP`, `_BOTTOM`, `_DELTA`, `_ROW`. `ViewportState` reads `SCROLLBACK_ROWS`, `TOTAL_ROWS`, and `VIEWPORT_ACTIVE` (measured: `1` at bottom, `0` when scrolled up).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_viewport.py -v`
Expected: 6 passed

- [ ] **Step 5: Add a reflow test**

```python
def test_resize_while_scrolled_clamps_safely():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        terminal.resize(10, 3)
        state = terminal.viewport
        assert 0 <= state.offset <= state.scrollback_rows
```

Run: `uv run pytest tests/test_viewport.py -v`
Expected: 7 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/emulator.py tests/test_viewport.py
git commit -m "feat: viewport scrolling via the struct-twin shim"
```

---

### Task 6: Theme, hard reset, and resize

**Files:**
- Create: `src/ghostty_textual/theme.py`
- Modify: `src/ghostty_textual/emulator.py`
- Test: `tests/test_theme.py`, `tests/test_hard_reset.py`

**Interfaces:**
- Produces: `TerminalTheme(foreground: Rgb, background: Rgb, cursor: Rgb | None, palette: tuple[Rgb, ...])` with `.from_textual(app_theme)`; `Terminal.set_theme(theme)`; `Terminal.hard_reset()`; `Terminal.theme -> TerminalTheme | None`; `Terminal.colour_override_foreground -> Rgb | None` (reads `GHOSTTY_TERMINAL_DATA_COLOR_FOREGROUND`, which returns `rc=-4` and therefore `None` when the guest has set no override).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_hard_reset.py
from ghostty_textual.emulator import Terminal


def test_hard_reset_clears_scrollback_and_title():
    with Terminal(20, 5, scrollback=1000) as terminal:
        terminal.feed(b"\x1b]0;old\x07")
        terminal.feed(b"".join(b"line%d\r\n" % i for i in range(40)))
        terminal.hard_reset()
        assert terminal.title in (None, "")
        assert terminal.viewport.scrollback_rows == 0


def test_hard_reset_clears_osc_colour_overrides():
    """The C reset preserves these -- which is why hard_reset recreates."""
    with Terminal(20, 5) as terminal:
        terminal.feed(b"\x1b]10;#ff0000\x07")
        terminal.hard_reset()
        frame = terminal.snapshot(force=True)
        assert frame is not None
        # The recreated terminal reports no override.
        assert terminal.colour_override_foreground is None


def test_hard_reset_leaves_the_terminal_usable():
    with Terminal(20, 5) as terminal:
        terminal.hard_reset()
        assert terminal.feed(b"\x1b[6n").pty_writes == (b"\x1b[1;1R",)


def test_hard_reset_preserves_configuration():
    with Terminal(20, 5, scrollback=77) as terminal:
        terminal.hard_reset()
        terminal.feed(b"".join(b"l%d\r\n" % i for i in range(300)))
        assert terminal.viewport.scrollback_rows <= 77
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_hard_reset.py -v`
Expected: FAIL — `AttributeError: 'Terminal' object has no attribute 'hard_reset'`

- [ ] **Step 3: Implement `hard_reset()` as free-and-recreate**

Free the terminal, render state, iterators, and encoders; drop the callback keepalive list; rebuild everything from the retained `cols`, `rows`, `scrollback`, `theme`, `clipboard`, `limits`. Reset the interner to generation 0 and force the next snapshot to be full.

Keep `ghostty_terminal_reset()` available separately for a guest-initiated RIS (`ESC c`), where preserving embedder colour configuration is correct.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_hard_reset.py -v`
Expected: 4 passed

- [ ] **Step 5: Implement `theme.py` and `set_theme`, with tests**

```python
# tests/test_theme.py
from ghostty_textual.emulator import Terminal
from ghostty_textual.theme import TerminalTheme

DARK = TerminalTheme(
    foreground=(200, 200, 200), background=(20, 20, 20), cursor=(255, 255, 255),
    palette=tuple((i, i, i) for i in range(256)),
)


def test_theme_is_applied_at_construction():
    with Terminal(20, 3, theme=DARK) as terminal:
        assert terminal.theme == DARK


def test_set_theme_forces_a_full_frame():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hi")
        terminal.snapshot()
        terminal.set_theme(DARK)
        frame = terminal.snapshot()
        assert frame is not None and frame.full_redraw
```

Run: `uv run pytest tests/test_theme.py -v`
Expected: 2 passed

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/theme.py src/ghostty_textual/emulator.py tests/test_theme.py tests/test_hard_reset.py
git commit -m "feat: theme configuration and free-and-recreate hard reset"
```

---

### Task 7: Input encoders

**Files:**
- Create: `src/ghostty_textual/keys.py`
- Modify: `src/ghostty_textual/emulator.py`
- Test: `tests/test_encoders.py`

**Interfaces:**
- Produces: `KeyEvent(key: str, text: str | None, ctrl: bool, alt: bool, shift: bool, meta: bool)`; `Terminal.encode_key(event) -> bytes | None`; `.encode_focus(bool) -> bytes | None`; `.encode_paste(str) -> bytes`; `.encode_mouse(MouseEvent) -> bytes | None`. `keys.from_textual(event) -> KeyEvent | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_encoders.py
from ghostty_textual.emulator import KeyEvent, Terminal


def test_plain_character():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="a", text="a")) == b"a"


def test_enter_is_carriage_return():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="enter", text=None)) == b"\r"


def test_ctrl_c():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="c", text="c", ctrl=True)) == b"\x03"


def test_cursor_keys_follow_decckm():
    """The gap ADR-0001 lists as knowingly accepted under pyte."""
    with Terminal(20, 3) as terminal:
        normal = terminal.encode_key(KeyEvent(key="up", text=None))
        terminal.feed(b"\x1b[?1h")  # DECCKM on: application cursor keys
        application = terminal.encode_key(KeyEvent(key="up", text=None))
        assert normal == b"\x1b[A"
        assert application == b"\x1bOA"


def test_paste_is_bracketed_when_the_mode_is_set():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_paste("hi") == b"hi"
        terminal.feed(b"\x1b[?2004h")
        assert terminal.encode_paste("hi") == b"\x1b[200~hi\x1b[201~"


def test_unsafe_paste_is_rejected():
    """ghostty_paste_is_safe guards control characters in an unbracketed paste."""
    with Terminal(20, 3) as terminal:
        assert terminal.encode_paste("rm -rf /\n") is None


def test_focus_events_only_when_enabled():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_focus(True) is None
        terminal.feed(b"\x1b[?1004h")
        assert terminal.encode_focus(True) == b"\x1b[I"
        assert terminal.encode_focus(False) == b"\x1b[O"


def test_unrepresentable_key_returns_none():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="f25", text=None)) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_encoders.py -v`
Expected: FAIL — `ImportError: cannot import name 'KeyEvent'`

- [ ] **Step 3: Implement the encoders**

**Call `ghostty_key_encoder_setopt_from_terminal(encoder, terminal)` before every `encode_key`.** That is what makes DECCKM, keypad mode, modifyOtherKeys, Kitty flags, and backarrow mode reflect current state rather than construction-time state — the whole reason for using libghostty's encoder instead of a hand-rolled table.

`encode_paste` returns `None` when `ghostty_paste_is_safe()` is false and bracketed paste is off.

`encode_mouse` is implemented here and tested headlessly. **No widget-level remote mouse in v1** (spec §6.8).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_encoders.py -v`
Expected: 8 passed

- [ ] **Step 5: Add the Textual translation layer and its test**

```python
# in tests/test_encoders.py
from ghostty_textual.keys import from_textual


class FakeKey:
    def __init__(self, key, character=None):
        self.key, self.character = key, character


def test_textual_key_names_translate():
    assert from_textual(FakeKey("ctrl+a")).ctrl
    assert from_textual(FakeKey("a", "a")).text == "a"
    assert from_textual(FakeKey("pageup")).key == "pageup"
```

Run: `uv run pytest tests/test_encoders.py -v`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/keys.py src/ghostty_textual/emulator.py tests/test_encoders.py
git commit -m "feat: key, paste, focus, and mouse encoders via libghostty"
```

---

### Task 8: TerminalView rendering and sizing

**Files:**
- Create: `src/ghostty_textual/widget.py`
- Test: `tests/test_widget_render.py`

**Interfaces:**
- Produces: `TerminalView(*, send, resize_transport=None, scrollback=5000, theme=None, reserved_keys=frozenset(), terminal=None, close_terminal=False)`; `.feed(bytes)`; `.sync_terminal_size() -> tuple[int, int]`; `.terminal`; messages `Resized`, `TitleChanged`, `Bell`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_widget_render.py
import pytest
from textual.app import App, ComposeResult
from ghostty_textual.widget import TerminalView


class Harness(App):
    def __init__(self):
        super().__init__()
        self.sent: list[bytes] = []
        self.view: TerminalView | None = None

    def compose(self) -> ComposeResult:
        self.view = TerminalView(send=self._send)
        yield self.view

    async def _send(self, data: bytes) -> None:
        self.sent.append(data)


async def test_fed_text_appears_in_a_strip():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        await app.workers.wait_for_complete()
        strip = app.view.render_line(0)
        assert "hello" in strip.text


async def test_terminal_is_sized_to_the_content_area():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        assert app.view.terminal.cols == 40


async def test_resize_transport_is_called_synchronously_on_mount():
    """Spec v2 §6.3: SSH must start at the real size, not 80x24."""
    seen: list[tuple[int, int]] = []
    app = Harness()
    app.view_factory = lambda: TerminalView(send=app._send, resize_transport=lambda c, r: seen.append((c, r)))
    async with app.run_test(size=(40, 10)):
        assert seen and seen[0] == (40, 10)


async def test_title_change_posts_a_message():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"\x1b]0;remote\x07")
        await app.workers.wait_for_complete()
        assert app.view.terminal.title == "remote"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_widget_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual.widget'`

- [ ] **Step 3: Implement rendering and sizing**

- Shadow buffer is `list[list[Cell]]` for the viewport. Applying a `Frame`: if `full_redraw`, replace wholesale; otherwise apply `row_patches` by index.
- **Guard on `frame.generation`.** If it differs from the shadow buffer's generation, discard patches and request `snapshot(force=True)` — this is the intern-rollover contract from Task 1.
- `render_line(y)` reads only the shadow buffer. Group runs by `style_id`, skip `width == 0` continuation cells, end with `.apply_offsets(0, y)`.
- Size from `content_size`, not `size`, so theme borders do not desync the transport. Call `resize_transport` **synchronously** in `on_mount`/`on_resize`, then post `Resized`. Coalesce duplicates; reject zero dimensions.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_widget_render.py -v`
Expected: 4 passed

- [ ] **Step 5: Add ownership and style-rendering tests**

```python
async def test_widget_closes_the_terminal_it_created():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        terminal = app.view.terminal
    assert terminal.closed


async def test_an_injected_terminal_is_borrowed_not_closed():
    """Spec v2 §3.5: injected means borrowed unless ownership is transferred."""
    injected = Terminal(40, 10)
    app = Harness(terminal=injected)
    async with app.run_test(size=(40, 10)):
        pass
    assert not injected.closed
    injected.close()


async def test_close_terminal_transfers_ownership_explicitly():
    injected = Terminal(40, 10)
    app = Harness(terminal=injected, close_terminal=True)
    async with app.run_test(size=(40, 10)):
        pass
    assert injected.closed


async def test_sgr_colour_reaches_the_strip():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"\x1b[31mred\x1b[0m")
        await app.workers.wait_for_complete()
        segments = list(app.view.render_line(0))
        assert any(seg.style and seg.style.color for seg in segments)
```

Run: `uv run pytest tests/test_widget_render.py -v`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/widget.py tests/test_widget_render.py
git commit -m "feat: TerminalView rendering with shadow buffer and sizing contract"
```

---

### Task 9: Ordered output, failure containment, and reset

**Files:**
- Modify: `src/ghostty_textual/widget.py`
- Test: `tests/test_widget_output.py`

**Interfaces:**
- Produces: `TerminalView.hard_reset()`; message `TerminalFailed(error)`; `.failed: bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_widget_output.py
async def test_query_replies_and_keystrokes_share_one_fifo():
    """A DSR reply interleaved into a keystroke would corrupt the input stream."""
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b[6n")
        await pilot.press("a")
        await app.workers.wait_for_complete()
        assert app.sent == [b"\x1b[1;6R", b"a"] or app.sent == [b"\x1b[1;1R", b"a"]


async def test_a_full_queue_fails_fast_rather_than_dropping():
    app = Harness(queue_size=1, stalled_send=True)
    async with app.run_test(size=(40, 10)):
        for _ in range(50):
            app.view.feed(b"\x1b[6n")
        await app.workers.wait_for_complete()
        assert app.view.failed


async def test_a_fatal_emulator_error_never_escapes_feed():
    """Escaping would reach SshSession._pump and recreate the ADR-0001 freeze."""
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.terminal.close()          # force a lifecycle violation
        app.view.feed(b"hello")            # must not raise
        assert app.view.failed


async def test_feeds_after_failure_are_ignored():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.terminal.close()
        app.view.feed(b"one")
        app.view.feed(b"two")
        assert app.view.failed


async def test_hard_reset_discards_the_previous_sessions_writes():
    """Queued bytes must never reach a reconnected process."""
    app = Harness(stalled_send=True)
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"\x1b[6n")
        app.view.hard_reset()
        app.release_send()
        await app.workers.wait_for_complete()
        assert app.sent == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_widget_output.py -v`
Expected: FAIL

- [ ] **Step 3: Implement the ordered writer and failure containment**

- One bounded `asyncio.Queue` plus a writer task. Sync path (`feed` → `pty_writes`) uses `put_nowait()`; `QueueFull` → `TerminalFailed`. Async paths (`on_key`, `on_paste`, focus) may `await put()`.
- `feed()` wraps the emulator call in `try/except GhosttyError`, sets `self._failed`, posts `TerminalFailed`, shuts the queue, and **returns normally**.
- `hard_reset()` drains the queue without sending, bumps a writer generation so in-flight sends from the old generation are abandoned, clears `_failed`, resets the shadow buffer, and forces a full snapshot.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_widget_output.py -v`
Expected: 5 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/widget.py tests/test_widget_output.py
git commit -m "feat: ordered write queue, failure containment, and reset discard"
```

---

### Task 10: Selection, scroll gestures, and cursor

**Files:**
- Modify: `src/ghostty_textual/widget.py`
- Test: `tests/test_widget_interaction.py`

**Interfaces:**
- Produces: `TerminalView.get_selection(selection) -> tuple[str, str] | None`; `MouseMode` enum with a single `LOCAL` member.

- [ ] **Step 1: Write the failing tests**

```python
async def test_selection_extracts_text_without_padding():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        await app.workers.wait_for_complete()
        text, _ = app.view.get_selection(Selection.from_offsets((0, 0), (5, 0)))
        assert text == "hello"


async def test_selection_skips_wide_continuation_cells():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed("界界".encode())
        await app.workers.wait_for_complete()
        text, _ = app.view.get_selection(Selection.from_offsets((0, 0), (4, 0)))
        assert text == "界界"


async def test_wheel_scrolls_local_history_and_sends_nothing():
    """ADR-0002: the mouse belongs to the pane, never to the remote app."""
    app = Harness()
    async with app.run_test(size=(40, 5)) as pilot:
        app.view.feed(b"".join(b"line%d\r\n" % i for i in range(40)))
        await app.workers.wait_for_complete()
        await pilot.hover(app.view)
        await pilot.mouse_scroll_up(app.view)
        assert app.sent == []
        assert not app.view.terminal.viewport.at_bottom


async def test_mouse_mode_defaults_to_local():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        assert app.view.mouse_mode is MouseMode.LOCAL


async def test_reserved_keys_are_neither_encoded_nor_stopped():
    app = Harness(reserved_keys=frozenset({"ctrl+w"}))
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.press("ctrl+w")
        assert app.sent == []


async def test_cursor_is_hidden_while_scrolled_away():
    app = Harness()
    async with app.run_test(size=(40, 5)):
        app.view.feed(b"".join(b"line%d\r\n" % i for i in range(40)))
        app.view.terminal.scroll_to_top()
        await app.workers.wait_for_complete()
        assert not app.view.cursor_visible
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_widget_interaction.py -v`

- [ ] **Step 3: Implement**

- `get_selection` reads the immutable shadow buffer, trims trailing padding, skips `width == 0` cells, joins hard breaks with `\n`.
- Wheel and `Shift+PageUp/Down` call `terminal.scroll_viewport(...)` then request a frame. **Never** call `encode_mouse` from the widget.
- Cursor from `CursorState`: block → reverse, underline → underline, bar → reverse (documented limitation). Blink timer off when unfocused; invalidate only the cursor row.
- `reserved_keys` is checked **before** encoding and the event is left unstopped so priority bindings fire.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_widget_interaction.py -v`
Expected: 6 passed

- [ ] **Step 5: Add the offsets regression test**

```python
async def test_render_line_applies_offsets():
    """Without this the compositor cannot map a click to a cell and selection
    silently yields nothing. ADR-0002 records the original bug."""
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        await app.workers.wait_for_complete()
        assert app.view.render_line(0)._offsets is not None
```

Run: `uv run pytest tests/test_widget_interaction.py -v`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/widget.py tests/test_widget_interaction.py
git commit -m "feat: selection, local scroll gestures, and cursor rendering"
```

---

### Task 11: Hyperlinks, clipboard policy, and rate limiting

**Files:**
- Modify: `src/ghostty_textual/widget.py`, `src/ghostty_textual/emulator.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: widget messages `LinkClicked(uri: str)` and `ClipboardWrite(text: str)`; emulator notification `ClipboardWritten(text: str)`; `Terminal.resolve_link(link_id) -> str | None`; `Terminal.kitty_image_storage_bytes -> int`.

**Naming, deliberately distinct:** `ClipboardWritten` is the *emulator* notification inside `TerminalEffects`; `ClipboardWrite` is the *Textual message* the widget posts after applying policy. Do not merge them — the emulator has no opinion about the UI, and the widget is where the policy decision is observable.

- [ ] **Step 1: Write the failing tests**

```python
async def test_clicking_a_link_posts_the_uri_and_opens_nothing():
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\")
        await app.workers.wait_for_complete()
        await pilot.click(app.view, offset=(1, 0))
        assert app.messages_of(LinkClicked)[0].uri == "https://example.com"


def test_oversized_link_uris_are_rejected():
    with Terminal(80, 24, limits=ResourceLimits(max_link_uri_bytes=32)) as terminal:
        terminal.feed(b"\x1b]8;;https://example.com/" + b"a" * 500 + b"\x1b\\x")
        frame = terminal.snapshot(force=True)
        assert frame.row_patches[0].cells[0].link_id is None


def test_clipboard_write_is_off_by_default():
    with Terminal(80, 24) as terminal:
        assert terminal.feed(b"\x1b]52;c;aGk=\x07").notifications == ()


def test_clipboard_write_is_delivered_when_enabled():
    policy = ClipboardPolicy(allow_write=True, max_bytes=1024)
    with Terminal(80, 24, clipboard=policy) as terminal:
        notifications = terminal.feed(b"\x1b]52;c;aGk=\x07").notifications
        assert any(isinstance(n, ClipboardWritten) and n.text == "hi" for n in notifications)


def test_oversized_clipboard_payloads_are_dropped():
    policy = ClipboardPolicy(allow_write=True, max_bytes=4)
    with Terminal(80, 24, clipboard=policy) as terminal:
        payload = base64.b64encode(b"x" * 100)
        assert terminal.feed(b"\x1b]52;c;" + payload + b"\x07").notifications == ()


def test_notification_floods_are_rate_limited():
    with Terminal(80, 24, limits=ResourceLimits(notification_rate_per_sec=10)) as terminal:
        effects = terminal.feed(b"\x07" * 1000)
        assert len(effects.notifications) <= 10


def test_kitty_image_storage_is_disabled():
    """Graphics are parsed but never retained in v1."""
    with Terminal(80, 24) as terminal:
        assert terminal.kitty_image_storage_bytes == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_security.py -v`

- [ ] **Step 3: Implement**

Set `GHOSTTY_TERMINAL_OPT_KITTY_IMAGE_STORAGE_LIMIT` to `0` and the `APC_MAX_BYTES` options from `ResourceLimits` at construction. Link interning is bounded exactly like style interning and shares the generation. `LinkClicked` carries the URI and the library never opens it — scheme policy is the application's.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_security.py -v`
Expected: 7 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/ tests/test_security.py
git commit -m "feat: hyperlink handling, clipboard policy, and resource limits"
```

---

### Task 12: Benchmarks, fuzzing, and packaging

**Files:**
- Create: `benchmarks/frame_extraction.py`, `tests/test_fuzz.py`, `.github/workflows/ci.yml`
- Test: `tests/test_packaging.py`

- [ ] **Step 1: Write the benchmark script**

```python
# benchmarks/frame_extraction.py
"""Measure frame extraction. Spec v2 §10: 120x40 full frame, p95 < 10 ms."""
import statistics, time
from ghostty_textual.emulator import Terminal

def main() -> None:
    with Terminal(120, 40, scrollback=5000) as terminal:
        terminal.feed(b"".join(b"row %d %s\r\n" % (i, b"x" * 100) for i in range(40)))
        full, single = [], []
        for _ in range(200):
            start = time.perf_counter()
            terminal.snapshot(force=True)
            full.append((time.perf_counter() - start) * 1000)
        for i in range(200):
            terminal.feed(b"\x1b[1;1H%d" % (i % 10))
            start = time.perf_counter()
            terminal.snapshot()
            single.append((time.perf_counter() - start) * 1000)
    for name, samples in (("full 120x40", full), ("single dirty row", single)):
        ordered = sorted(samples)
        p95 = ordered[int(len(ordered) * 0.95)]
        print(f"{name}: median {statistics.median(samples):.3f} ms  p95 {p95:.3f} ms")

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and record the baseline**

Run: `uv run python benchmarks/frame_extraction.py`
Expected: full-frame p95 under 10 ms. If it is not, stop and profile before continuing — the dirty-row design exists to make this cheap, and missing the budget means something is re-reading clean rows.

- [ ] **Step 3: Add the budget as a test**

```python
# tests/test_performance.py
import time, pytest
from ghostty_textual.emulator import Terminal

@pytest.mark.performance
def test_full_frame_extraction_within_budget():
    with Terminal(120, 40, scrollback=5000) as terminal:
        terminal.feed(b"".join(b"row %d %s\r\n" % (i, b"x" * 100) for i in range(40)))
        samples = []
        for _ in range(100):
            start = time.perf_counter()
            terminal.snapshot(force=True)
            samples.append((time.perf_counter() - start) * 1000)
    p95 = sorted(samples)[95]
    assert p95 < 10.0, f"p95 {p95:.2f} ms exceeds the 10 ms budget"
```

- [ ] **Step 4: Add subprocess fuzzing**

```python
# tests/test_fuzz.py
import random, subprocess, sys, pytest

RUNNER = """
import sys, random
from ghostty_textual.emulator import Terminal
seed = int(sys.argv[1])
rng = random.Random(seed)
with Terminal(80, 24) as terminal:
    for _ in range(500):
        terminal.feed(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64))))
        terminal.snapshot()
"""

@pytest.mark.parametrize("seed", range(8))
def test_random_bytes_never_crash_or_hang(seed: int) -> None:
    """A segfault cannot be reported from inside the pytest process."""
    result = subprocess.run(
        [sys.executable, "-c", RUNNER, str(seed)], capture_output=True, timeout=60
    )
    assert result.returncode == 0, result.stderr.decode()[-2000:]
```

Run: `uv run pytest tests/test_fuzz.py -v`
Expected: 8 passed

- [ ] **Step 5: Add packaging tests and CI**

```python
# tests/test_packaging.py
def test_package_imports_without_loading_native_code():
    """Spec v2 §7: importable on platforms with no wheel."""
    import subprocess, sys
    result = subprocess.run(
        [sys.executable, "-c", "import ghostty_textual; print(ghostty_textual.__version__)"],
        capture_output=True,
    )
    assert result.returncode == 0
```

CI matrix: `macos-14` (arm64), `macos-13` (x86_64), `ubuntu-24.04` (x86_64), `ubuntu-24.04-arm` (aarch64). Each job runs `uv sync --all-extras`, `uv run ruff check src tests`, `uv run pytest -q`. Add a **separate scheduled job** that installs the latest `pyghostty` instead of the pin and runs `tests/test_abi.py`, so upstream ABI drift surfaces as a failing nightly rather than a broken release.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests benchmarks
git add benchmarks tests/test_fuzz.py tests/test_performance.py tests/test_packaging.py .github
git commit -m "feat: performance budget, subprocess fuzzing, and CI matrix"
```

---

## Definition of done

- [ ] `uv run pytest -q` green; `uv run ruff check src tests` clean
- [ ] `emulator.py`, `cells.py`, `theme.py`, `keys.py` import no Textual; `widget.py` imports no `pyghostty`/`cffi`
- [ ] Frame extraction p95 under 10 ms at 120×40
- [ ] Fuzz runs clean in subprocesses
- [ ] CI green on all four wheel platforms
- [ ] Scheduled unpinned-ABI job exists and passes

Once green, write the `pysshmanager` cutover plan (spec §8 and §13), which is scoped separately and includes the ~1 day test rewrite.
