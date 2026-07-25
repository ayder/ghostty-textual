# ghostty-textual Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable Textual terminal widget over `libghostty-vt` that never owns a process, so applications that already manage their own PTY lifecycle can embed a modern terminal.

**Architecture:** Two layers with one native boundary. The private `_native.py`/`_render.py` modules are the only code that touches C; `emulator.py` exposes a headless `Terminal` (bytes in, effects out, self-contained frames on demand); `widget.py` renders frames into Textual strips and encodes input. `libghostty-vt`'s C API is explicitly unstable, so all churn is confined below `emulator.py`.

**Tech Stack:** Python 3.12+, Textual 8.2.8, `pyghostty==0.1.0` (CFFI ABI mode over a bundled `libghostty-vt` shared library), pytest, ruff, hatchling.

**Spec:** [`../specs/2026-07-25-ghostty-textual-design-v2.md`](../specs/2026-07-25-ghostty-textual-design-v2.md)

**Revision:** v2 of this plan. v1 was reviewed and its Task 2 rested on a false premise — see [Review disposition](#review-disposition).

## Scope

Spec §12 steps **3–7**: the library, from frame extraction through security policy. Steps 1–2 are **already committed** — `_native.py` plus `test_abi.py`, `test_effects.py`, `test_reset.py`, `test_resize.py`, 31 tests passing.

Step 8, the `pysshmanager` cutover, is **excluded**. Different repository; the library must be independently green first. It gets its own plan after Task 13.

## Global Constraints

- Python `>=3.12`. Textual `>=8.2.8,<9`. `pyghostty==0.1.0` — pinned exactly, because we bind its **private** `_ffi`/`_cdef` modules.
- **The library never spawns a process, opens a PTY, or owns a transport.** Bytes in, bytes out.
- `emulator.py`, `cells.py`, `theme.py`, `keys.py` must not import `textual`. `widget.py` must not import `pyghostty` or `cffi`.
- **Use the public C accessors.** Never decode a `GhosttyCell`/`GhosttyRow` bit layout. They are opaque `uint64_t` handles with `ghostty_cell_get`/`ghostty_row_get`; a private fast path may only be added if a measured budget miss justifies it, and then it must be optional and differential-tested against the public path.
- `libghostty-vt` is **not thread-safe**. Every `Terminal` operation happens on the owning thread/event loop.
- Binding errors are **fatal and typed** (`GhosttyError`). Malformed *remote* bytes are not errors — libghostty handles them.
- No `__del__` for correctness. Explicit `close()` and context managers only.
- CFFI callback objects are held on the owning Python object until after the C object is freed.
- All public dataclasses are `frozen=True, slots=True`. **Frames are self-contained** — no public object holds a C reference or an out-of-band table.
- **Every task that introduces a new native call appends to `REQUIRED_SYMBOLS` in `_native.py` and extends `tests/test_abi.py` in the same commit.**
- Line length 100. `ruff check src tests` must pass. Every task ends green.

## Verified C API

All confirmed against the installed `pyghostty==0.1.0` wheel.

**Cells and rows are opaque handles, not packed integers:**

```c
typedef uint64_t GhosttyCell;
typedef uint64_t GhosttyRow;
GhosttyResult ghostty_cell_get(GhosttyCell, GhosttyCellData, void *out);
GhosttyResult ghostty_cell_get_multi(GhosttyCell, size_t, const GhosttyCellData*, void**, size_t*);
```

`GhosttyCellData`: `CODEPOINT`, `WIDE`, `HAS_TEXT`, `HAS_STYLING`, `STYLE_ID`, `HAS_HYPERLINK`, `CONTENT_TAG`, `COLOR_RGB`, `COLOR_PALETTE`, `PROTECTED`, `SEMANTIC_CONTENT`.

`GhosttyCellWide`: `NARROW`, `WIDE`, `SPACER_TAIL`, `SPACER_HEAD` — this is the width model; do not infer width from text.

`GhosttyCellContentTag`: `CODEPOINT`, `CODEPOINT_GRAPHEME`, `BG_COLOR_PALETTE`, `BG_COLOR_RGB`.

**Render state:**

```c
ghostty_render_state_new/free/update/begin_update/end_update
ghostty_render_state_get(state, GhosttyRenderStateData, void* out)
ghostty_render_state_set(state, GhosttyRenderStateOption, const void*)   // OPTION_DIRTY
ghostty_render_state_colors_get(state, GhosttyRenderStateColors*)
ghostty_render_state_row_iterator_new/next/free
ghostty_render_state_row_get(iterator, GhosttyRenderStateRowData, void* out)
ghostty_render_state_row_set(iterator, GhosttyRenderStateRowOption, const void*)
ghostty_render_state_row_cells_new/next/select/get/free
```

Binding: `render_state_get(state, DATA_ROW_ITERATOR, iterator)` binds an iterator to a state; `render_state_row_get(iterator, ROW_DATA_CELLS, cells)` binds a cells iterator to a row; `render_state_row_cells_get(cells, ROW_CELLS_DATA_RAW, GhosttyCell*)` yields the cell handle.

Row data enum values: `INVALID=0`, `DIRTY=1`, `RAW=2`, `CELLS=3`, `SELECTION=4`.

**Clearing dirty state takes two calls.** Per-row: `render_state_row_set(iterator, ROW_OPTION_DIRTY, &false)`. Global: `render_state_set(state, OPTION_DIRTY, &false)`. Clearing one does not clear the other.

**Cursor** comes from viewport fields: `CURSOR_VIEWPORT_X/Y/HAS_VALUE/WIDE_TAIL`, `CURSOR_VISIBLE`, `CURSOR_VISUAL_STYLE`, `CURSOR_BLINKING`. Coordinates are **undefined unless `CURSOR_VIEWPORT_HAS_VALUE`**. `GhosttyTerminalCursorStyle`: `BAR`, `BLOCK`, `UNDERLINE`, `BLOCK_HOLLOW`.

**Scrollbar** — the source of viewport offset:

```c
typedef struct { uint64_t total; uint64_t offset; uint64_t len; } GhosttyTerminalScrollbar;
```

**Output types are enum-sized or struct-typed, not `bool*`/`uint16_t*` by default.** Task 1 introduces typed helpers; never allocate an out-pointer ad hoc.

**Measured** (macOS arm64, released wheel): public-accessor full-frame extraction at 120×40 is ≈4 ms, against a 10 ms budget. The public path is fast enough; there is no reason to touch private layout.

## File Structure

| File | Responsibility | Touches C? |
|---|---|---|
| `_native.py` | Loader, ABI verification, union twins, **typed getters** | yes (partly done) |
| `_render.py` | Render state → `RowPatch`/`CursorState`/`ViewportState`; dirty acknowledgement | yes |
| `cells.py` | `Cell`, `CellStyle`, `StyleInterner`, `LinkInterner`, `InternerFull` | no |
| `theme.py` | `TerminalTheme`, palette → Ghostty colour options | no |
| `emulator.py` | `Terminal`: lifecycle, `feed`, `snapshot`, viewport, modes, encoders | yes |
| `keys.py` | Textual `events.Key` → `KeyEvent` | no |
| `widget.py` | `TerminalView`: shadow buffer, strips, input, selection | no |

`_render.py` joining the native boundary is a deliberate refinement of spec §1. The one-boundary principle holds — the boundary is the private `_`-prefixed modules — but render-state iteration deserves its own file.

---

### Task 1: Cell model, interners, and typed native getters

**Files:**
- Create: `src/ghostty_textual/cells.py`
- Modify: `src/ghostty_textual/_native.py`
- Test: `tests/test_cells.py`, `tests/test_native_getters.py`

**Interfaces:**
- Produces: `Cell(text, width, style_id, link_id)`; `CellStyle`; `InternerFull(Exception)`; `StyleInterner`/`LinkInterner` with `.generation`, `.intern(value) -> int` (**raises `InternerFull`**), `.resolve(id)`, `.table() -> tuple[...]`, `.rollover()`; `Native.get_bool/get_u16/get_u32/get_enum/get_struct`.

`LinkInterner` lands here, not in Task 11: `Cell.link_id` exists from this task, so the type that assigns it must exist too. Task 11 adds only the *policy* around URIs.

- [ ] **Step 1: Write the failing interner tests**

```python
# tests/test_cells.py
import pytest
from ghostty_textual.cells import Cell, CellStyle, InternerFull, LinkInterner, StyleInterner

RED = CellStyle(fg=(255, 0, 0))
BLUE = CellStyle(fg=(0, 0, 255))


def test_identical_styles_intern_to_the_same_id():
    interner = StyleInterner(limit=16)
    assert interner.intern(RED) == interner.intern(CellStyle(fg=(255, 0, 0)))


def test_different_styles_get_different_ids():
    interner = StyleInterner(limit=16)
    assert interner.intern(RED) != interner.intern(BLUE)


def test_resolve_returns_the_original():
    interner = StyleInterner(limit=16)
    assert interner.resolve(interner.intern(RED)) == RED


def test_resolve_rejects_negative_ids():
    """Python would happily return the last entry for -1."""
    interner = StyleInterner(limit=16)
    interner.intern(RED)
    with pytest.raises(KeyError):
        interner.resolve(-1)


def test_intern_raises_before_exceeding_the_limit():
    """A hard signal, not a flag: the table must never actually grow past limit."""
    interner = StyleInterner(limit=4)
    for value in range(4):
        interner.intern(CellStyle(fg=(value, 0, 0)))
    with pytest.raises(InternerFull):
        interner.intern(CellStyle(fg=(99, 0, 0)))
    assert len(interner.table()) == 4


def test_rollover_bumps_generation_and_empties_the_table():
    interner = StyleInterner(limit=4)
    start = interner.generation
    interner.intern(RED)
    interner.rollover()
    assert interner.generation == start + 1
    assert interner.table() == ()
    with pytest.raises(KeyError):
        interner.resolve(0)


def test_table_is_ordered_by_id():
    interner = StyleInterner(limit=16)
    red_id, blue_id = interner.intern(RED), interner.intern(BLUE)
    table = interner.table()
    assert table[red_id] == RED and table[blue_id] == BLUE


def test_link_interner_bounds_uri_length():
    interner = LinkInterner(limit=8, max_uri_bytes=16)
    assert interner.intern("https://a.example") is None  # too long -> not interned
    assert interner.resolve(interner.intern("https://a.co")) == "https://a.co"


def test_continuation_cell_has_zero_width():
    assert Cell(text="", width=0, style_id=0).width == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cells.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual.cells'`

- [ ] **Step 3: Implement `cells.py`**

```python
"""Cell model and bounded interning. No C, no Textual."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Rgb = tuple[int, int, int]


class InternerFull(Exception):
    """The table is at its limit. The caller must roll over and restart the frame."""


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
    underline: int = 0


DEFAULT_STYLE = CellStyle()


@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    width: Literal[0, 1, 2]  # 0 = SPACER_TAIL/HEAD continuation
    style_id: int
    link_id: int | None = None


@dataclass(slots=True)
class StyleInterner:
    limit: int = 4096
    generation: int = 0
    _forward: dict[CellStyle, int] = field(default_factory=dict)
    _reverse: list[CellStyle] = field(default_factory=list)

    def intern(self, style: CellStyle) -> int:
        existing = self._forward.get(style)
        if existing is not None:
            return existing
        if len(self._reverse) >= self.limit:
            raise InternerFull(f"style table is at its limit of {self.limit}")
        style_id = len(self._reverse)
        self._forward[style] = style_id
        self._reverse.append(style)
        return style_id

    def resolve(self, style_id: int) -> CellStyle:
        if style_id < 0 or style_id >= len(self._reverse):
            raise KeyError(style_id)
        return self._reverse[style_id]

    def table(self) -> tuple[CellStyle, ...]:
        return tuple(self._reverse)

    def rollover(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self.generation += 1


@dataclass(slots=True)
class LinkInterner:
    """Same shape, plus a URI length bound. Over-long URIs are dropped, not interned."""

    limit: int = 1024
    max_uri_bytes: int = 2048
    generation: int = 0
    _forward: dict[str, int] = field(default_factory=dict)
    _reverse: list[str] = field(default_factory=list)

    def intern(self, uri: str) -> int | None:
        if len(uri.encode("utf-8")) > self.max_uri_bytes:
            return None
        existing = self._forward.get(uri)
        if existing is not None:
            return existing
        if len(self._reverse) >= self.limit:
            raise InternerFull(f"link table is at its limit of {self.limit}")
        link_id = len(self._reverse)
        self._forward[uri] = link_id
        self._reverse.append(uri)
        return link_id

    def resolve(self, link_id: int) -> str:
        if link_id < 0 or link_id >= len(self._reverse):
            raise KeyError(link_id)
        return self._reverse[link_id]

    def table(self) -> tuple[str, ...]:
        return tuple(self._reverse)

    def rollover(self) -> None:
        self._forward.clear()
        self._reverse.clear()
        self.generation += 1
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_cells.py -v`
Expected: 9 passed

- [ ] **Step 5: Add typed getters to `_native.py` with tests**

Ad-hoc `ffi.new("bool*")` is how you silently misread an enum-sized output. Centralise it:

```python
# in Native
def get_bool(self, getter, handle, key, what) -> bool:
    out = self.ffi.new("bool*")
    self.check(getter(handle, key, out), what)
    return bool(out[0])

def get_enum(self, getter, handle, key, what) -> int:
    """Enum outputs are int-sized, NOT bool/uint16. Reading them narrow is UB."""
    out = self.ffi.new("int*")
    self.check(getter(handle, key, out), what)
    return int(out[0])

def get_u16(self, getter, handle, key, what) -> int: ...
def get_u32(self, getter, handle, key, what) -> int: ...
def get_struct(self, getter, handle, key, ctype, what): ...
```

```python
# tests/test_native_getters.py
def test_enum_output_is_read_int_sized(native):
    """GHOSTTY_RENDER_STATE_DATA_DIRTY and CURSOR_VISUAL_STYLE are enums."""
    from tests.conftest import make_terminal
    from ghostty_textual._render import RenderState

    harness = make_terminal(native, cols=10, rows=2)
    state = RenderState(native)
    try:
        harness.feed(b"hi")
        state.update(harness.terminal)
        assert isinstance(state.is_dirty(), bool)
    finally:
        state.close()
        harness.close()
```

(That test depends on Task 2; mark it `xfail(strict=False)` here and unmark in Task 2 Step 6.)

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/cells.py src/ghostty_textual/_native.py tests/test_cells.py tests/test_native_getters.py
git commit -m "feat: cell model, bounded interners with hard overflow, typed native getters"
```

---

### Task 2: Render state via the public cell ABI

**Files:**
- Create: `src/ghostty_textual/_render.py`
- Modify: `src/ghostty_textual/_native.py` (`REQUIRED_SYMBOLS`), `tests/test_abi.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `Native` typed getters; `Cell`, `CellStyle`, `StyleInterner`, `LinkInterner`, `InternerFull`.
- Produces: `RowPatch(y, cells)`; `CursorState(x, y, visible, style, blinking, wide_tail)`; `ViewportState(at_bottom, offset, scrollback_rows, total_rows)`; `RenderState` with `.update(terminal)`, `.is_dirty()`, `.read_rows(styles, links, *, force) -> tuple[RowPatch, ...]`, `.read_cursor()`, `.clear_global_dirty()`, `.close()`.

`ViewportState` is defined **here**, not in Task 5, because `Frame` (Task 4) references it before Task 5 runs.

> **v1 of this plan was wrong here.** It described cells as packed `uint32` values and scheduled a reverse-engineering spike. `GhosttyCell` is an opaque `uint64_t` with public accessors covering every question that spike would have asked. There is no spike. Use `ghostty_cell_get`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render.py
from ghostty_textual._render import RenderState
from ghostty_textual.cells import LinkInterner, StyleInterner
from tests.conftest import make_terminal


def _read(state, harness, *, force=True):
    state.update(harness.terminal)
    return state.read_rows(StyleInterner(), LinkInterner(), force=force)


def test_reading_rows_returns_the_fed_text(native):
    harness, state = make_terminal(native, cols=20, rows=3), None
    try:
        state = RenderState(native)
        harness.feed(b"hello")
        rows = _read(state, harness)
        assert "".join(c.text for c in rows[0].cells).rstrip() == "hello"
    finally:
        state and state.close()
        harness.close()


def test_wide_character_produces_a_spacer_tail(native):
    """Width comes from GhosttyCellWide, never inferred from the text."""
    harness, state = make_terminal(native, cols=10, rows=1), None
    try:
        state = RenderState(native)
        harness.feed("界b".encode())
        cells = _read(state, harness)[0].cells
        assert (cells[0].text, cells[0].width) == ("界", 2)
        assert cells[1].width == 0
        assert cells[2].text == "b"
    finally:
        state and state.close()
        harness.close()


def test_only_dirty_rows_are_returned(native):
    harness, state = make_terminal(native, cols=20, rows=5), None
    styles, links = StyleInterner(), LinkInterner()
    try:
        state = RenderState(native)
        harness.feed(b"one\r\ntwo\r\nthree")
        state.update(harness.terminal)
        state.read_rows(styles, links, force=True)
        state.clear_global_dirty()

        harness.feed(b"\x1b[1;1Hx")
        state.update(harness.terminal)
        assert [p.y for p in state.read_rows(styles, links, force=False)] == [0]
    finally:
        state and state.close()
        harness.close()


def test_clean_state_reports_nothing(native):
    harness, state = make_terminal(native, cols=10, rows=2), None
    styles, links = StyleInterner(), LinkInterner()
    try:
        state = RenderState(native)
        harness.feed(b"hi")
        state.update(harness.terminal)
        state.read_rows(styles, links, force=True)
        state.clear_global_dirty()
        state.update(harness.terminal)
        assert state.read_rows(styles, links, force=False) == ()
    finally:
        state and state.close()
        harness.close()


def test_cursor_uses_viewport_coordinates(native):
    harness, state = make_terminal(native, cols=20, rows=3), None
    try:
        state = RenderState(native)
        harness.feed(b"abc")
        state.update(harness.terminal)
        cursor = state.read_cursor()
        assert (cursor.x, cursor.y, cursor.visible) == (3, 0, True)
    finally:
        state and state.close()
        harness.close()


def test_cursor_coordinates_are_not_read_when_absent(native):
    """CURSOR_VIEWPORT_X/Y are undefined unless HAS_VALUE; must report invisible."""
    harness, state = make_terminal(native, cols=20, rows=3, scrollback=500), None
    try:
        state = RenderState(native)
        harness.feed(b"".join(b"l%d\r\n" % i for i in range(60)))
        native.scroll_viewport(harness.terminal, native.lib.GHOSTTY_SCROLL_VIEWPORT_TOP)
        state.update(harness.terminal)
        assert not state.read_cursor().visible
    finally:
        state and state.close()
        harness.close()


def test_identical_styles_share_one_id(native):
    harness, state = make_terminal(native, cols=20, rows=1), None
    styles = StyleInterner()
    try:
        state = RenderState(native)
        harness.feed(b"\x1b[31maaa\x1b[0m")
        state.update(harness.terminal)
        cells = state.read_rows(styles, LinkInterner(), force=True)[0].cells
        assert len({c.style_id for c in cells[:3]}) == 1
    finally:
        state and state.close()
        harness.close()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ghostty_textual._render'`

- [ ] **Step 3: Implement `_render.py` against the public accessors**

The verified read flow, one cell at a time:

```python
WIDTH_BY_WIDE = {0: 1, 1: 2, 2: 0, 3: 0}  # NARROW, WIDE, SPACER_TAIL, SPACER_HEAD


def _read_cell(self, styles: StyleInterner, links: LinkInterner) -> Cell:
    ffi, lib, native = self._native.ffi, self._native.lib, self._native
    handle = ffi.new("GhosttyCell*")
    native.check(
        lib.ghostty_render_state_row_cells_get(
            self._cells[0], lib.GHOSTTY_RENDER_STATE_ROW_CELLS_DATA_RAW, handle
        ),
        "cell RAW",
    )
    cell = handle[0]

    def data(key: str, reader):
        return reader(lib.ghostty_cell_get, cell, getattr(lib, f"GHOSTTY_CELL_DATA_{key}"), key)

    width = WIDTH_BY_WIDE[data("WIDE", native.get_enum)]
    text = ""
    if data("HAS_TEXT", native.get_bool):
        codepoint = data("CODEPOINT", native.get_u32)
        text = chr(codepoint) if codepoint else ""
        if data("CONTENT_TAG", native.get_enum) == lib.GHOSTTY_CELL_CONTENT_CODEPOINT_GRAPHEME:
            text = self._read_grapheme(cell, fallback=text)

    style = DEFAULT_STYLE
    if data("HAS_STYLING", native.get_bool):
        style = self._read_style(cell)

    link_id = None
    if data("HAS_HYPERLINK", native.get_bool):
        link_id = self._read_link(cell, links)

    return Cell(text=text, width=width, style_id=styles.intern(style), link_id=link_id)
```

Verified against `'\x1b[31ma\x1b[0m界b'` in a 10×1 terminal:

```
x=0 codepoint=97    'a'  wide=NARROW      has_text=True  has_styling=True  style_id=1
x=1 codepoint=30028 '界'  wide=WIDE        has_text=True  has_styling=False style_id=0
x=2 codepoint=0          wide=SPACER_TAIL has_text=False
x=3 codepoint=98    'b'  wide=NARROW      has_text=True
```

Three things to get right:

1. `read_rows` clears **per-row** dirty via `row_set(iterator, ROW_OPTION_DIRTY, &false)`. A separate `clear_global_dirty()` calls `render_state_set(state, OPTION_DIRTY, &false)`. Both are required.
2. `read_cursor()` checks `CURSOR_VIEWPORT_HAS_VALUE` **before** reading X/Y, and returns `visible=False` without reading them when it is false.
3. `InternerFull` from `styles.intern` or `links.intern` propagates — Task 4's `snapshot()` handles it. Do not catch it here.

Use `ghostty_cell_get_multi` for the common key set once the single-key version is green and tested; keep the single-key path as the differential reference.

- [ ] **Step 4: Add `ViewportState` reading from the scrollbar struct**

```python
@dataclass(frozen=True, slots=True)
class ViewportState:
    at_bottom: bool
    offset: int
    scrollback_rows: int
    total_rows: int
```

`offset` comes from `GhosttyTerminalScrollbar {total, offset, len}` via
`ghostty_terminal_get(terminal, GHOSTTY_TERMINAL_DATA_SCROLLBAR, &scrollbar)` —
`SCROLLBACK_ROWS`/`TOTAL_ROWS`/`VIEWPORT_ACTIVE` alone cannot supply it.

- [ ] **Step 5: Extend `REQUIRED_SYMBOLS` and `test_abi.py`**

Add `ghostty_cell_get`, `ghostty_cell_get_multi`, `ghostty_row_get`, `ghostty_render_state_set`, `ghostty_render_state_row_cells_select`, `ghostty_render_state_colors_get`. Add an assertion that `GhosttyCell` and `GhosttyRow` are 8 bytes, so a future change from opaque handle to struct fails loudly.

- [ ] **Step 6: Run everything; unmark the Task 1 xfail**

Run: `uv run pytest -q`
Expected: all pass

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/_render.py src/ghostty_textual/_native.py tests/test_render.py tests/test_abi.py tests/test_native_getters.py
git commit -m "feat: render state extraction via the public cell accessor ABI"
```

---

### Task 3: Terminal lifecycle, feed, and effects

**Files:**
- Create: `src/ghostty_textual/emulator.py`
- Test: `tests/test_emulator_lifecycle.py`

**Interfaces:**
- Produces: `Terminal(cols, rows, *, scrollback=5000, theme=None, clipboard=ClipboardPolicy(), limits=ResourceLimits())`; `.feed(bytes) -> TerminalEffects`; `.close()`; `.closed`; context manager. `TerminalEffects(pty_writes, notifications)`. `ClipboardPolicy(allow_write=False, max_bytes=1_000_000)`. `ResourceLimits(max_interned_styles=4096, max_interned_links=1024, max_link_uri_bytes=2048, kitty_image_storage_bytes=0, apc_max_bytes=8192, apc_max_bytes_kitty=8192, notification_rate_per_sec=60)`. Notifications `TitleChanged`, `BellRang`, `ClipboardWritten`.

`ResourceLimits` carries the APC fields **now** so Task 12 can derive terminal options from it without changing the type.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_emulator_lifecycle.py
import pytest
from ghostty_textual.emulator import BellRang, Terminal, TitleChanged


def test_feed_returns_query_replies():
    with Terminal(80, 24) as terminal:
        assert terminal.feed(b"\x1b[6n").pty_writes == (b"\x1b[1;1R",)


def test_pty_writes_preserve_order():
    with Terminal(80, 24) as terminal:
        assert terminal.feed(b"\x1b[6n\x1b[c").pty_writes == (b"\x1b[1;1R", b"\x1b[?62;22c")


def test_title_change_is_notified():
    with Terminal(80, 24) as terminal:
        assert TitleChanged(title="hello") in terminal.feed(b"\x1b]0;hello\x07").notifications
        assert terminal.title == "hello"


def test_bell_is_notified():
    with Terminal(80, 24) as terminal:
        assert any(isinstance(n, BellRang) for n in terminal.feed(b"\x07").notifications)


def test_clipboard_write_refused_by_default():
    with Terminal(80, 24) as terminal:
        assert terminal.feed(b"\x1b]52;c;aGVsbG8=\x07").notifications == ()


def test_close_is_idempotent():
    terminal = Terminal(80, 24)
    terminal.close()
    terminal.close()
    assert terminal.closed


def test_operations_after_close_raise():
    terminal = Terminal(80, 24)
    terminal.close()
    with pytest.raises(RuntimeError):
        terminal.feed(b"x")


def test_malformed_bytes_are_not_errors():
    with Terminal(80, 24) as terminal:
        terminal.feed(b"\x1b[?4m")          # the CSI that froze pyte
        terminal.feed(b"\xff\xfe\x00garbage")
        terminal.feed(b"\x1b[999999999J")


def test_create_feed_close_loop_does_not_leak():
    for _ in range(200):
        with Terminal(80, 24) as terminal:
            terminal.feed(b"hello\r\n")


def test_use_from_another_thread_is_rejected():
    import threading

    failures: list[BaseException] = []
    with Terminal(80, 24) as terminal:
        def other() -> None:
            try:
                terminal.feed(b"x")
            except BaseException as exc:
                failures.append(exc)

        thread = threading.Thread(target=other)
        thread.start()
        thread.join()
    assert failures and isinstance(failures[0], RuntimeError)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_emulator_lifecycle.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement lifecycle and feed**

- `WRITE_PTY` callback **only appends to a list**. No `vt_write`, no await, no blocking — it fires synchronously inside `ghostty_terminal_vt_write`.
- Exceptions inside any CFFI callback are captured to `self._callback_error` and re-raised as `GhosttyError` **after** `vt_write` returns. Never unwind through C.
- Callback objects live in `self._keepalive` until after `ghostty_terminal_free`.
- Record `threading.get_ident()` at construction; check it in every public method.
- Register `OPT_SIZE` returning cached rows/columns with `cell_width = cell_height = 0` (spec §3.4), and the `KITTY_IMAGE_STORAGE_LIMIT`/`APC_MAX_BYTES` options from `limits`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_emulator_lifecycle.py -v`
Expected: 10 passed

- [ ] **Step 5: Extend `REQUIRED_SYMBOLS`/`test_abi.py`** with any newly called symbols.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/emulator.py src/ghostty_textual/_native.py tests/
git commit -m "feat: Terminal lifecycle, feed, and effect collection"
```

---

### Task 4: Self-contained frames and snapshot

**Files:**
- Modify: `src/ghostty_textual/emulator.py`
- Test: `tests/test_emulator_frames.py`

**Interfaces:**
- Produces: `Terminal.snapshot(*, force=False) -> Frame | None`;

```python
@dataclass(frozen=True, slots=True)
class Frame:
    generation: int
    cols: int
    rows: int
    full_redraw: bool
    row_patches: tuple[RowPatch, ...]
    styles: tuple[CellStyle, ...]   # index == style_id, complete for this generation
    links: tuple[str, ...]          # index == link_id
    cursor: CursorState
    viewport: ViewportState
    frame_pending: bool
```

**Frames are self-contained.** v1 of this plan gave the widget `style_id` with no way to resolve it and no way to swap tables atomically on rollover. Carrying the complete generation-scoped tables inside the frame makes the widget's update a single assignment and removes the whole class of torn-generation bugs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_emulator_frames.py
from ghostty_textual.cells import DEFAULT_STYLE
from ghostty_textual.emulator import ResourceLimits, Terminal


def test_snapshot_returns_a_frame_after_output():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        frame = terminal.snapshot()
        assert "".join(c.text for c in frame.row_patches[0].cells).rstrip() == "hello"


def test_snapshot_with_no_change_returns_none():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        assert terminal.snapshot() is None


def test_force_returns_a_full_frame():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        frame = terminal.snapshot(force=True)
        assert frame.full_redraw and len(frame.row_patches) == 3


def test_only_changed_rows_are_patched():
    with Terminal(20, 5) as terminal:
        terminal.feed(b"a\r\nb\r\nc")
        terminal.snapshot()
        terminal.feed(b"\x1b[1;1Hz")
        assert [p.y for p in terminal.snapshot().row_patches] == [0]


def test_frame_carries_a_resolvable_style_table():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[31mred\x1b[0m")
        frame = terminal.snapshot()
        cell = frame.row_patches[0].cells[0]
        assert frame.styles[cell.style_id].fg is not None


def test_resize_produces_a_frame_without_a_feed():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hello")
        terminal.snapshot()
        terminal.resize(40, 6)
        frame = terminal.snapshot()
        assert (frame.cols, frame.rows) == (40, 6)


def test_style_overflow_rolls_over_within_one_snapshot():
    """The rollover must complete in the snapshot that overflows, not the next one."""
    limits = ResourceLimits(max_interned_styles=4)
    with Terminal(20, 3, limits=limits) as terminal:
        terminal.feed(b"hi")
        first = terminal.snapshot()
        for value in range(20):
            terminal.feed(f"\x1b[38;2;{value};0;0mx".encode())
        second = terminal.snapshot()
        assert second.generation > first.generation
        assert second.full_redraw
        assert len(second.styles) <= 4
        for patch in second.row_patches:
            for cell in patch.cells:
                assert 0 <= cell.style_id < len(second.styles)
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_emulator_frames.py -v`
Expected: FAIL — `AttributeError: 'Terminal' object has no attribute 'snapshot'`

- [ ] **Step 3: Implement `snapshot()` with restart-on-overflow**

```python
def snapshot(self, *, force: bool = False) -> Frame | None:
    self._assert_usable()
    self._render.update(self._terminal)
    if not force and not self._render.is_dirty():
        return None
    for attempt in (1, 2):
        try:
            patches = self._render.read_rows(self._styles, self._links, force=force)
        except InternerFull:
            # The table filled mid-extraction. Discard the partial result, roll
            # over both tables, and restart as a forced full extraction. The
            # second attempt cannot overflow: a full frame interns at most
            # cols*rows distinct styles, and the limit is validated above that.
            self._styles.rollover()
            self._links.rollover()
            self._render.update(self._terminal)
            force = True
            continue
        break
    else:
        raise GhosttyError("style interning failed twice in one snapshot")
    cursor = self._render.read_cursor()
    viewport = self._read_viewport()
    self._render.clear_global_dirty()
    return Frame(
        generation=self._styles.generation,
        cols=self._cols, rows=self._rows,
        full_redraw=force,
        row_patches=patches,
        styles=self._styles.table(),
        links=self._links.table(),
        cursor=cursor, viewport=viewport,
        frame_pending=self._frame_pending,
    )
```

Validate at construction that `max_interned_styles >= cols * rows` is achievable, or clamp and document; otherwise the restart guarantee does not hold.

Document on the method: dirty state is cleared before returning, so a caller that raises while applying a frame must recover with `snapshot(force=True)`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_emulator_frames.py -v`
Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests
git add src/ghostty_textual/emulator.py tests/test_emulator_frames.py
git commit -m "feat: self-contained frames with restart-on-interner-overflow"
```

---

### Task 5: Viewport and scrollback

**Files:** Modify `src/ghostty_textual/emulator.py`; Test `tests/test_viewport.py`

**Interfaces:** `Terminal.scroll_viewport(ScrollRequest)`, `.scroll_to_top()`, `.scroll_to_bottom()`, `.viewport -> ViewportState`. `ScrollRequest` = `ScrollTop() | ScrollBottom() | ScrollDelta(n) | ScrollToRow(n)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_viewport.py
from ghostty_textual.emulator import ScrollDelta, Terminal


def _fill(terminal, count=40):
    terminal.feed(b"".join(b"line%d\r\n" % i for i in range(count)))


def test_fresh_terminal_is_at_bottom():
    with Terminal(20, 5) as terminal:
        assert terminal.viewport.at_bottom


def test_scroll_to_top_then_bottom():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        assert not terminal.viewport.at_bottom
        terminal.scroll_to_bottom()
        assert terminal.viewport.at_bottom


def test_offset_is_reported_from_the_scrollbar():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_bottom()
        bottom = terminal.viewport.offset
        terminal.scroll_to_top()
        assert terminal.viewport.offset != bottom


def test_scrolled_content_stays_anchored_while_output_arrives():
    """The behaviour spec v1 got wrong. A real terminal does not drift."""
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        before = ["".join(c.text for c in p.cells).rstrip()
                  for p in terminal.snapshot(force=True).row_patches]
        terminal.feed(b"newline\r\n" * 3)
        after = ["".join(c.text for c in p.cells).rstrip()
                 for p in terminal.snapshot(force=True).row_patches]
        assert before == after


def test_scrollback_is_bounded():
    with Terminal(20, 5, scrollback=10) as terminal:
        _fill(terminal, count=200)
        assert terminal.viewport.scrollback_rows <= 10


def test_scroll_delta_moves():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_viewport(ScrollDelta(-3))
        assert not terminal.viewport.at_bottom


def test_resize_while_scrolled_clamps_safely():
    with Terminal(20, 5, scrollback=1000) as terminal:
        _fill(terminal)
        terminal.scroll_to_top()
        terminal.resize(10, 3)
        state = terminal.viewport
        assert 0 <= state.offset <= state.total_rows
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_viewport.py -v`

- [ ] **Step 3: Implement** using `Native.scroll_viewport` (the struct twin, already verified) and the `GhosttyTerminalScrollbar` read from Task 2.

- [ ] **Step 4: Run to verify they pass** — Expected: 7 passed

- [ ] **Step 5: Extend `REQUIRED_SYMBOLS`/`test_abi.py`**

- [ ] **Step 6: Lint and commit**

```bash
git add src/ghostty_textual/emulator.py tests/test_viewport.py src/ghostty_textual/_native.py tests/test_abi.py
git commit -m "feat: viewport scrolling with scrollbar-derived offset"
```

---

### Task 6: Terminal modes and synchronized output

**Files:** Modify `src/ghostty_textual/emulator.py`; Test `tests/test_modes.py`

**Interfaces:** `Terminal.modes -> TerminalModes(bracketed_paste, focus_events, app_cursor_keys, alt_screen, mouse_tracking, mouse_encoding, sync_output)`; `Frame.frame_pending`.

This task exists because spec v2 specifies `TerminalModes` and synchronized-output behaviour, and v1 of this plan had no task for either.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_modes.py
from ghostty_textual.emulator import Terminal


def test_bracketed_paste_mode_is_observable():
    with Terminal(20, 3) as terminal:
        assert not terminal.modes.bracketed_paste
        terminal.feed(b"\x1b[?2004h")
        assert terminal.modes.bracketed_paste


def test_application_cursor_keys_mode():
    with Terminal(20, 3) as terminal:
        assert not terminal.modes.app_cursor_keys
        terminal.feed(b"\x1b[?1h")
        assert terminal.modes.app_cursor_keys


def test_alternate_screen_mode():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[?1049h")
        assert terminal.modes.alt_screen
        terminal.feed(b"\x1b[?1049l")
        assert not terminal.modes.alt_screen


def test_focus_event_mode():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[?1004h")
        assert terminal.modes.focus_events


def test_synchronized_output_is_observable():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[?2026h")
        assert terminal.modes.sync_output


def test_unterminated_synchronized_frame_still_yields_a_snapshot():
    """A missing terminator must never freeze the widget indefinitely."""
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[?2026h")   # begin, never end
        terminal.feed(b"hello")
        assert terminal.snapshot(force=True) is not None
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_modes.py -v`

- [ ] **Step 3: Implement modes via `ghostty_terminal_mode_get`**

Then **measure before building anything**: does libghostty already withhold render-state dirty flags inside a synchronized frame? Write a scratch script that feeds `?2026h`, some output, then `?2026l`, checking `is_dirty()` at each point. Record the answer as a comment.

- If libghostty already gates: `frame_pending` simply reports `modes.sync_output` and no withholding logic is written.
- If it does not: `snapshot()` withholds while `sync_output` is set, with a bounded timeout (default 150 ms) after which it snapshots anyway.

Spec §3.1 requires measuring first rather than reimplementing DECSET 2026 speculatively.

- [ ] **Step 4: Run to verify they pass** — Expected: 6 passed

- [ ] **Step 5: Extend `REQUIRED_SYMBOLS`/`test_abi.py`** with `ghostty_terminal_mode_get`.

- [ ] **Step 6: Lint and commit**

```bash
git add src/ghostty_textual/emulator.py tests/test_modes.py src/ghostty_textual/_native.py tests/test_abi.py
git commit -m "feat: terminal mode queries and synchronized output handling"
```

---

### Task 7: Theme and hard reset

**Files:** Create `src/ghostty_textual/theme.py`; Modify `emulator.py`; Test `tests/test_theme.py`, `tests/test_hard_reset.py`

**Interfaces:** `TerminalTheme(foreground, background, cursor, palette)` with `.from_textual(app_theme)`; `Terminal.set_theme(theme)`; `.theme`; `.hard_reset()`; `.colour_override_foreground -> Rgb | None` (reads `COLOR_FOREGROUND`, which returns `rc=-4` — hence `None` — when the guest set no override).

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
    """ghostty_terminal_reset() preserves these -- which is why we recreate."""
    with Terminal(20, 5) as terminal:
        terminal.feed(b"\x1b]10;#ff0000\x07")
        assert terminal.colour_override_foreground is not None
        terminal.hard_reset()
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


def test_hard_reset_resets_interner_generation():
    with Terminal(20, 5) as terminal:
        terminal.feed(b"\x1b[31mx\x1b[0m")
        terminal.snapshot()
        terminal.hard_reset()
        assert terminal.snapshot(force=True).generation == 0
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement `hard_reset()` as free-and-recreate** — free terminal, render state, iterators, encoders; drop the keepalive list; rebuild from retained `cols`, `rows`, `scrollback`, `theme`, `clipboard`, `limits`; reset both interners to generation 0.

Keep `ghostty_terminal_reset()` available separately for a guest-initiated RIS (`ESC c`), where preserving embedder colours is correct.

- [ ] **Step 4: Run to verify they pass** — Expected: 5 passed

- [ ] **Step 5: Implement `theme.py` and `set_theme` with tests**

```python
# tests/test_theme.py
DARK = TerminalTheme(foreground=(200, 200, 200), background=(20, 20, 20),
                     cursor=(255, 255, 255), palette=tuple((i, i, i) for i in range(256)))


def test_theme_is_applied_at_construction():
    with Terminal(20, 3, theme=DARK) as terminal:
        assert terminal.theme == DARK


def test_set_theme_forces_a_full_frame():
    with Terminal(20, 3) as terminal:
        terminal.feed(b"hi")
        terminal.snapshot()
        terminal.set_theme(DARK)
        assert terminal.snapshot().full_redraw
```

- [ ] **Step 6: Lint and commit**

```bash
git add src/ghostty_textual/theme.py src/ghostty_textual/emulator.py tests/test_theme.py tests/test_hard_reset.py
git commit -m "feat: theme configuration and free-and-recreate hard reset"
```

---

### Task 8: Input encoders

**Files:** Create `src/ghostty_textual/keys.py`; Modify `emulator.py`; Test `tests/test_encoders.py`

**Interfaces:** `KeyEvent(key, text=None, ctrl=False, alt=False, shift=False, meta=False)`; `Terminal.encode_key(event) -> bytes | None`; `.encode_focus(bool) -> bytes | None`; **`.encode_paste(str) -> bytes | None`**; `.encode_mouse(MouseEvent) -> bytes | None`; `keys.from_textual(event) -> KeyEvent | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_encoders.py
from ghostty_textual.emulator import KeyEvent, Terminal


def test_plain_character():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="a", text="a")) == b"a"


def test_enter_is_carriage_return():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="enter")) == b"\r"


def test_ctrl_c():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="c", text="c", ctrl=True)) == b"\x03"


def test_cursor_keys_follow_decckm():
    """The gap ADR-0001 lists as knowingly accepted under pyte."""
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="up")) == b"\x1b[A"
        terminal.feed(b"\x1b[?1h")
        assert terminal.encode_key(KeyEvent(key="up")) == b"\x1bOA"


def test_paste_is_bracketed_when_the_mode_is_set():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_paste("hi") == b"hi"
        terminal.feed(b"\x1b[?2004h")
        assert terminal.encode_paste("hi") == b"\x1b[200~hi\x1b[201~"


def test_unsafe_unbracketed_paste_is_refused():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_paste("rm -rf /\n") is None


def test_unsafe_paste_is_allowed_when_bracketed():
    """Bracketing is what makes control characters safe to deliver."""
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[?2004h")
        assert terminal.encode_paste("a\nb") is not None


def test_focus_events_only_when_enabled():
    with Terminal(20, 3) as terminal:
        assert terminal.encode_focus(True) is None
        terminal.feed(b"\x1b[?1004h")
        assert terminal.encode_focus(True) == b"\x1b[I"
        assert terminal.encode_focus(False) == b"\x1b[O"


def test_a_key_with_no_representation_returns_none():
    """Textual can deliver names Ghostty has no keycode for."""
    with Terminal(20, 3) as terminal:
        assert terminal.encode_key(KeyEvent(key="unknown_synthetic_key")) is None
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

**Call `ghostty_key_encoder_setopt_from_terminal(encoder, terminal)` before every `encode_key`.** That is what makes DECCKM, keypad mode, modifyOtherKeys, Kitty flags, and backarrow mode reflect current state — the entire reason for using libghostty's encoder over a hand-rolled table.

`encode_paste` returns `None` when `ghostty_paste_is_safe()` is false **and** bracketed paste is off.

`encode_mouse` is implemented and tested headlessly. **No widget-level remote mouse in v1** (spec §6.8).

Note: F13–F25 *are* valid Ghostty keycodes; do not use them as the unrepresentable-key case.

- [ ] **Step 4: Run to verify they pass** — Expected: 9 passed

- [ ] **Step 5: Add `keys.from_textual` and its tests**

```python
class FakeKey:
    def __init__(self, key, character=None):
        self.key, self.character = key, character


def test_textual_key_names_translate():
    assert from_textual(FakeKey("ctrl+a")).ctrl
    assert from_textual(FakeKey("a", "a")).text == "a"
    assert from_textual(FakeKey("pageup")).key == "pageup"
```

- [ ] **Step 6: Extend `REQUIRED_SYMBOLS`/`test_abi.py`** with the key-event lifecycle symbols.

- [ ] **Step 7: Lint and commit**

```bash
git add src/ghostty_textual/keys.py src/ghostty_textual/emulator.py tests/test_encoders.py src/ghostty_textual/_native.py tests/test_abi.py
git commit -m "feat: key, paste, focus, and mouse encoders via libghostty"
```

---

### Task 9: TerminalView rendering and sizing

**Files:** Create `src/ghostty_textual/widget.py`; Create `tests/widget_harness.py`; Test `tests/test_widget_render.py`

**Interfaces:** `TerminalView(*, send, resize_transport=None, scrollback=5000, theme=None, reserved_keys=frozenset(), terminal=None, close_terminal=False, queue_size=256)`; `.feed(bytes)`; `.sync_terminal_size() -> tuple[int, int]`; `.terminal`; messages `Resized`, `TitleChanged`, `Bell`.

- [ ] **Step 1: Write the shared harness once**

v1 of this plan drifted three incompatible `Harness` definitions across tasks. One definition, in its own module, used by every widget test:

```python
# tests/widget_harness.py
from __future__ import annotations

import asyncio
from textual.app import App, ComposeResult
from textual.message import Message
from ghostty_textual.emulator import Terminal
from ghostty_textual.widget import TerminalView


class Harness(App):
    """One configurable app for every widget test."""

    def __init__(
        self,
        *,
        terminal: Terminal | None = None,
        close_terminal: bool = False,
        reserved_keys: frozenset[str] = frozenset(),
        queue_size: int = 256,
        stalled_send: bool = False,
        resize_transport=None,
    ) -> None:
        super().__init__()
        self.sent: list[bytes] = []
        self.captured: list[Message] = []
        self.view: TerminalView | None = None
        self._gate = asyncio.Event()
        if not stalled_send:
            self._gate.set()
        self._options = dict(
            terminal=terminal, close_terminal=close_terminal,
            reserved_keys=reserved_keys, queue_size=queue_size,
            resize_transport=resize_transport,
        )

    def compose(self) -> ComposeResult:
        self.view = TerminalView(send=self._send, **self._options)
        yield self.view

    async def _send(self, data: bytes) -> None:
        await self._gate.wait()
        self.sent.append(data)

    def release_send(self) -> None:
        self._gate.set()

    def messages_of(self, kind: type[Message]) -> list[Message]:
        return [m for m in self.captured if isinstance(m, kind)]

    async def on_message(self, message: Message) -> None:
        self.captured.append(message)
```

- [ ] **Step 2: Write the failing render tests**

```python
# tests/test_widget_render.py
from ghostty_textual.emulator import Terminal
from tests.widget_harness import Harness


async def test_fed_text_appears_in_a_strip():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        assert "hello" in app.view.render_line(0).text


async def test_terminal_is_sized_to_the_content_area():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        assert app.view.terminal.cols == 40


async def test_resize_transport_is_called_synchronously_on_mount():
    """Spec v2 §6.3: SSH must start at the real size, not 80x24."""
    seen: list[tuple[int, int]] = []
    app = Harness(resize_transport=lambda c, r: seen.append((c, r)))
    async with app.run_test(size=(40, 10)):
        assert seen and seen[0] == (40, 10)


async def test_sgr_colour_reaches_the_strip():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"\x1b[31mred\x1b[0m")
        assert any(s.style and s.style.color for s in app.view.render_line(0))


async def test_widget_closes_the_terminal_it_created():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        terminal = app.view.terminal
    assert terminal.closed


async def test_an_injected_terminal_is_borrowed_not_closed():
    injected = Terminal(40, 10)
    app = Harness(terminal=injected)
    async with app.run_test(size=(40, 10)):
        pass
    assert not injected.closed
    injected.close()


async def test_close_terminal_transfers_ownership():
    injected = Terminal(40, 10)
    app = Harness(terminal=injected, close_terminal=True)
    async with app.run_test(size=(40, 10)):
        pass
    assert injected.closed


async def test_generation_change_replaces_the_shadow_atomically():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        first = app.view.render_line(0).text
        app.view.terminal.hard_reset()
        app.view.feed(b"world")
        assert first != app.view.render_line(0).text
```

- [ ] **Step 3: Run to verify they fail** — `uv run pytest tests/test_widget_render.py -v`

- [ ] **Step 4: Implement rendering and sizing**

- Shadow buffer holds `(generation, rows, styles, links)` **as one tuple**, replaced by a single assignment. Applying a `Frame`: if `frame.generation != shadow.generation` or `frame.full_redraw`, replace wholesale; otherwise apply `row_patches` by index. A generation mismatch on a patch-only frame means discard and `snapshot(force=True)`.
- `render_line(y)` reads only the shadow buffer, resolving styles through the frame's own table. Group runs by `style_id`, skip `width == 0` cells, end with `.apply_offsets(0, y)`.
- Size from `content_size`, not `size`. Call `resize_transport` **synchronously** in `on_mount`/`on_resize`, then post `Resized`. Coalesce duplicates; reject zero dimensions.

- [ ] **Step 5: Run to verify they pass** — Expected: 8 passed

- [ ] **Step 6: Lint and commit**

```bash
git add src/ghostty_textual/widget.py tests/widget_harness.py tests/test_widget_render.py
git commit -m "feat: TerminalView rendering with self-contained frame application"
```

---

### Task 10: Ordered output, failure containment, async reset

**Files:** Modify `src/ghostty_textual/widget.py`; Test `tests/test_widget_output.py`

**Interfaces:** **`async TerminalView.reset_io()`**; `TerminalView.hard_reset()` (sync, terminal-only); message `TerminalFailed(error)`; `.failed: bool`.

**A synchronous reset cannot cancel an in-flight `await send(...)`.** v1 of this plan claimed it could. I/O reset is therefore `async` — it cancels the writer task, awaits its exit, drains the queue, and bumps the writer generation. `feed()` stays synchronous; only the reset is awaited.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_widget_output.py
from ghostty_textual.widget import TerminalFailed
from tests.widget_harness import Harness


async def test_query_replies_and_keystrokes_share_one_fifo():
    """A DSR reply interleaved into a keystroke would corrupt the input stream."""
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b[6n")
        await pilot.press("a")
        await pilot.pause()
        assert app.sent[-1] == b"a"
        assert app.sent[0].endswith(b"R")


async def test_a_full_queue_fails_fast():
    app = Harness(queue_size=1, stalled_send=True)
    async with app.run_test(size=(40, 10)):
        for _ in range(200):
            app.view.feed(b"\x1b[6n")
        assert app.view.failed
        assert app.messages_of(TerminalFailed)


async def test_a_fatal_emulator_error_never_escapes_feed():
    """Escaping would reach SshSession._pump and recreate the ADR-0001 freeze."""
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.terminal.close()
        app.view.feed(b"hello")          # must not raise
        assert app.view.failed


async def test_feeds_after_failure_are_ignored():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.terminal.close()
        app.view.feed(b"one")
        app.view.feed(b"two")
        assert app.view.failed


async def test_reset_io_discards_the_previous_sessions_writes():
    """Queued bytes must never reach a reconnected process."""
    app = Harness(stalled_send=True)
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"\x1b[6n")
        await app.view.reset_io()
        app.release_send()
        await app.workers.wait_for_complete()
        assert app.sent == []


async def test_reset_io_cancels_an_in_flight_send():
    app = Harness(stalled_send=True)
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.press("a")           # writer blocks inside send()
        await pilot.pause()
        await app.view.reset_io()        # must cancel and await, not race
        app.release_send()
        await app.workers.wait_for_complete()
        assert app.sent == []
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

- One bounded `asyncio.Queue(queue_size)` plus a writer task. Sync path (`feed` → `pty_writes`) uses `put_nowait()`; `QueueFull` → `TerminalFailed`. Async paths (`on_key`, `on_paste`, focus) may `await put()`.
- `feed()` wraps the emulator call in `try/except GhosttyError`, sets `_failed`, posts `TerminalFailed`, shuts the queue, and **returns normally**.
- `async reset_io()`: bump writer generation → cancel the writer task → `await` it → drain the queue without sending → start a fresh writer → clear `_failed`. The generation check means a send that completes during cancellation is discarded rather than recorded.
- `on_unmount` awaits `reset_io()` so a final chunk racing pane removal is dropped deterministically.

- [ ] **Step 4: Run to verify they pass** — Expected: 6 passed

- [ ] **Step 5: Lint and commit**

```bash
git add src/ghostty_textual/widget.py tests/test_widget_output.py
git commit -m "feat: ordered write queue, failure containment, async I/O reset"
```

---

### Task 11: Selection, scroll gestures, cursor

**Files:** Modify `src/ghostty_textual/widget.py`; Test `tests/test_widget_interaction.py`

**Interfaces:** `TerminalView.get_selection(selection) -> tuple[str, str] | None`; `MouseMode` enum with a single `LOCAL` member; `.cursor_visible`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_selection_extracts_text_without_padding():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        text, _ = app.view.get_selection(Selection.from_offsets((0, 0), (5, 0)))
        assert text == "hello"


async def test_selection_skips_spacer_tail_cells():
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed("界界".encode())
        text, _ = app.view.get_selection(Selection.from_offsets((0, 0), (4, 0)))
        assert text == "界界"


async def test_wheel_scrolls_locally_and_sends_nothing():
    """ADR-0002: the mouse belongs to the pane, never to the remote app."""
    app = Harness()
    async with app.run_test(size=(40, 5)) as pilot:
        app.view.feed(b"".join(b"line%d\r\n" % i for i in range(40)))
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
        app.view.refresh_frame()
        assert not app.view.cursor_visible


async def test_render_line_applies_offsets():
    """Without this the compositor cannot map a click to a cell and selection
    silently yields nothing. ADR-0002 records the original bug."""
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view.feed(b"hello")
        assert app.view.render_line(0)._offsets is not None
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement**

- `get_selection` reads the immutable shadow, trims trailing padding, skips `width == 0`, joins hard breaks with `\n`.
- Wheel and `Shift+PageUp/Down` call `terminal.scroll_viewport(...)` then request a frame. **Never** call `encode_mouse` from the widget.
- Cursor from `CursorState`: `BLOCK` → reverse, `UNDERLINE` → underline, `BLOCK_HOLLOW` → reverse, `BAR` → reverse (documented limitation). Blink timer off when unfocused; invalidate only the cursor row.
- `reserved_keys` checked **before** encoding; the event is left unstopped so priority bindings fire.

- [ ] **Step 4: Run to verify they pass** — Expected: 7 passed

- [ ] **Step 5: Lint and commit**

```bash
git add src/ghostty_textual/widget.py tests/test_widget_interaction.py
git commit -m "feat: selection, local scroll gestures, and cursor rendering"
```

---

### Task 12: Hyperlink and clipboard policy

**Files:** Modify `widget.py`, `emulator.py`; Test `tests/test_security.py`

**Interfaces:** widget messages `LinkClicked(uri)`, `ClipboardWrite(text)`; emulator notification `ClipboardWritten(text)`; `Terminal.resolve_link(link_id) -> str | None`.

**Naming, deliberately distinct:** `ClipboardWritten` is the *emulator* notification inside `TerminalEffects`; `ClipboardWrite` is the *Textual message* the widget posts after applying policy. The emulator has no opinion about the UI; the widget is where the policy decision becomes observable.

Interning and generation semantics already exist from Task 1; this task adds only policy.

- [ ] **Step 1: Write the failing tests**

```python
import base64
from ghostty_textual.emulator import ClipboardPolicy, ClipboardWritten, ResourceLimits, Terminal


async def test_clicking_a_link_posts_the_uri_and_opens_nothing():
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\")
        await pilot.click(app.view, offset=(1, 0))
        assert app.messages_of(LinkClicked)[0].uri == "https://example.com"


def test_oversized_link_uris_are_not_interned():
    limits = ResourceLimits(max_link_uri_bytes=32)
    with Terminal(80, 24, limits=limits) as terminal:
        terminal.feed(b"\x1b]8;;https://example.com/" + b"a" * 500 + b"\x1b\\x")
        assert terminal.snapshot(force=True).row_patches[0].cells[0].link_id is None


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
    limits = ResourceLimits(notification_rate_per_sec=10)
    with Terminal(80, 24, limits=limits) as terminal:
        assert len(terminal.feed(b"\x07" * 1000).notifications) <= 10


def test_kitty_image_storage_is_disabled():
    with Terminal(80, 24) as terminal:
        assert terminal.kitty_image_storage_bytes == 0
```

- [ ] **Step 2: Run to verify they fail**

- [ ] **Step 3: Implement** — link resolution via the grid-reference/hyperlink ABI; `LinkClicked` carries the URI and the library **never opens it**; scheme policy belongs to the application.

- [ ] **Step 4: Run to verify they pass** — Expected: 7 passed

- [ ] **Step 5: Extend `REQUIRED_SYMBOLS`/`test_abi.py`** with the hyperlink/grid-ref symbols.

- [ ] **Step 6: Lint and commit**

```bash
git add src/ghostty_textual/ tests/test_security.py
git commit -m "feat: hyperlink handling, clipboard policy, and resource limits"
```

---

### Task 13: Benchmarks, fuzzing, packaging, CI

**Files:** Create `benchmarks/frame_extraction.py`, `tests/test_fuzz.py`, `tests/test_performance.py`, `tests/test_packaging.py`, `.github/workflows/ci.yml`

- [ ] **Step 1: Write the benchmark script**

```python
# benchmarks/frame_extraction.py
"""Spec v2 §10: 120x40 full frame, p95 < 10 ms. Reference measurement: ~4 ms."""
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
        print(f"{name}: median {statistics.median(samples):.3f} ms  "
              f"p95 {ordered[int(len(ordered) * 0.95)]:.3f} ms")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it and record the baseline**

Run: `uv run python benchmarks/frame_extraction.py`
Expected: full-frame p95 under 10 ms (reference ≈4 ms). If it is not, profile before continuing — do **not** reach for a private packed-layout fast path until the public path is proven insufficient, and if you do, keep it optional and differential-tested against the public path.

- [ ] **Step 3: Add the budget and fuzz tests**

```python
# tests/test_performance.py
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

```python
# tests/test_fuzz.py
RUNNER = """
import sys, random
from ghostty_textual.emulator import Terminal
rng = random.Random(int(sys.argv[1]))
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

- [ ] **Step 4: Write a real packaging test**

Re-importing the source tree proves nothing about the wheel. Build it, install it into a clean throwaway environment, and check the artefact:

```python
# tests/test_packaging.py
import subprocess, sys, sysconfig, zipfile
from pathlib import Path
import pytest

CHECK = (
    "import ghostty_textual as g;"
    "assert g.__version__;"
    "assert {'GhosttyError', 'GhosttyUnavailable'} <= set(g.__all__);"
    "from ghostty_textual.emulator import Terminal;"
    "t = Terminal(20, 3); t.feed(b'hi'); assert t.snapshot(force=True); t.close();"
    "print('ok')"
)


@pytest.mark.packaging
def test_wheel_installs_and_works_in_a_clean_environment(tmp_path: Path) -> None:
    subprocess.run([sys.executable, "-m", "build", "--wheel", "-o", str(tmp_path)],
                   check=True, capture_output=True)
    wheel = next(tmp_path.glob("*.whl"))

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert any(n.endswith("ghostty_textual/py.typed") for n in names), "py.typed not shipped"
    assert any("licenses/" in n or n.endswith("LICENSE") for n in names), "licence not shipped"

    env = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(env)], check=True)
    python = env / ("Scripts" if sysconfig.get_platform().startswith("win") else "bin") / "python"
    subprocess.run([str(python), "-m", "pip", "install", "-q", str(wheel)], check=True)
    result = subprocess.run([str(python), "-c", CHECK], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_package_imports_without_loading_native_code() -> None:
    """Spec v2 §7: importable on platforms with no wheel."""
    result = subprocess.run(
        [sys.executable, "-c", "import ghostty_textual; print(ghostty_textual.__version__)"],
        capture_output=True,
    )
    assert result.returncode == 0
```

Add a `LICENSE` file and confirm `py.typed` is included by `[tool.hatch.build.targets.wheel]`.

- [ ] **Step 5: Write CI**

Matrix over the four wheel platforms. **Confirm current runner labels against
<https://github.com/actions/runner-images> when you write this** — `macos-13` is
retired; use the current ARM and Intel macOS labels (at time of writing,
`macos-15` and `macos-15-intel`) plus `ubuntu-24.04` and `ubuntu-24.04-arm`.

Each job: `uv sync --all-extras`, `uv run ruff check src tests benchmarks`, `uv run pytest -q`.

The scheduled ABI-drift job must **not** let `uv run` re-sync back to the pin:

```yaml
  abi-drift:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv sync --all-extras
      - run: uv pip install --upgrade pyghostty        # deliberately off-lockfile
      - run: uv run --no-sync pytest tests/test_abi.py -v   # --no-sync preserves it
```

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests benchmarks
git add benchmarks tests/test_fuzz.py tests/test_performance.py tests/test_packaging.py .github LICENSE
git commit -m "feat: performance budget, subprocess fuzzing, wheel packaging test, CI"
```

---

## Definition of done

- [ ] `uv run pytest -q` green; `uv run ruff check src tests benchmarks` clean
- [ ] `emulator.py`, `cells.py`, `theme.py`, `keys.py` import no Textual; `widget.py` imports no `pyghostty`/`cffi`
- [ ] No code decodes a `GhosttyCell`/`GhosttyRow` bit layout
- [ ] `REQUIRED_SYMBOLS` covers every symbol the library calls, asserted in `test_abi.py`
- [ ] Frame extraction p95 under 10 ms at 120×40
- [ ] Fuzz runs clean in subprocesses
- [ ] Wheel installs into a clean environment, exports its public API, ships `py.typed` and a licence
- [ ] CI green on all four wheel platforms; scheduled ABI-drift job exists and does not re-sync the pin

Then write the `pysshmanager` cutover plan (spec §8 and §13), including the ~1 day test rewrite.

## Review disposition

| Finding | Disposition |
|---|---|
| **Task 2 raw-cell premise is wrong** | **Accepted — verified.** `GhosttyCell`/`GhosttyRow` are opaque `uint64_t` with `ghostty_cell_get`; `GhosttyCellWide` supplies SPACER_TAIL/HEAD; `HAS_STYLING`/`STYLE_ID`/`HAS_HYPERLINK` answer the rest. The spike is deleted. The v1 `raw >> 2` observation came from reading a `uint64_t` handle through a `uint32_t*` — an accidental correlation with private packing |
| `GRAPHEMES_UTF8` rc=-3 means buffer too small | Accepted; grapheme text is read via the render-state grapheme buffer API |
| Interner rollover cannot work as sequenced | Accepted — `InternerFull` raised **before** insertion; `snapshot()` catches, rolls over, discards the partial extraction, restarts forced (Task 1, Task 4) |
| `resolve()` accepts negative ids | Accepted (Task 1) |
| Frames carry `style_id` with no table | Accepted — self-contained frames carry complete generation-scoped `styles` and `links` tables (Task 4) |
| Hyperlinks arrive too late | Accepted — `LinkInterner` moves to Task 1; Task 12 keeps only policy |
| Unsafe FFI output types | Accepted — typed getters in `_native.py`; enums read int-sized; cursor coordinates gated on `HAS_VALUE`; global dirty cleared via `render_state_set(OPTION_DIRTY)` (Tasks 1, 2) |
| `REQUIRED_SYMBOLS` must grow per task | Accepted — now a global constraint with a step in every task that adds native calls |
| Sync `hard_reset()` cannot cancel an in-flight send | Accepted — `async reset_io()` cancels and awaits the writer; `feed()` stays sync (Task 10) |
| `ViewportState.offset` needs the scrollbar struct | Accepted — `GhosttyTerminalScrollbar {total, offset, len}`, verified present (Task 2) |
| `ViewportState` used before it is defined | Accepted — moved to Task 2 |
| `encode_paste` return type | Accepted — `bytes \| None` (Task 8) |
| F25 is a valid Ghostty key | Accepted — test uses a synthetic unknown name |
| Task 8 harness is inconsistent | Accepted — one `tests/widget_harness.py`, written before its first use (Task 9) |
| `TerminalModes`/synchronized output had no task | Accepted — new Task 6, measurement-first per spec §3.1 |
| `ResourceLimits` needs APC fields | Accepted — declared in Task 3 |
| Packaging test too weak | Accepted — builds a wheel, installs into a clean venv, checks exports, `py.typed`, licence (Task 13) |
| Scheduled job re-syncs the pin | Accepted — `uv run --no-sync` |
| `macos-13` is retired | Accepted — current labels, with an instruction to confirm against the runner-images table at write time |
