# ghostty-textual — design v2

- **Date:** 2026-07-25
- **Status:** Proposed
- **Supersedes:** [`2026-07-25-ghostty-textual-design.md`](2026-07-25-ghostty-textual-design.md) (v1, left unmodified)
- **Incorporates:** [`sol-5.6-textual-design-review.md`](sol-5.6-textual-design-review.md)
- **First consumer:** `pysshmanager`

v1 chose the right boundary and got several contract details wrong. This
revision keeps the boundary, fixes the contract, and replaces v1's speculation
with measurements taken against the released `pyghostty` 0.1.0 wheel on macOS
arm64.

## 1. Decision (unchanged from v1)

Build `ghostty-textual`: a reusable, published Textual terminal widget library
over `libghostty-vt`, bound through **`pyghostty`'s low-level `_ffi`/`lib`**
rather than its high-level `core.Terminal`.

Two layers, one native boundary:

```
src/ghostty_textual/
  __init__.py       public exports (lazy — importable on unsupported platforms)
  _native.py        loader, ABI compatibility check, union-twin shims
  emulator.py       Terminal — the only file that touches C
  cells.py          Cell, CellStyle, bounded interning
  theme.py          TerminalTheme, palette → Ghostty default colors
  keys.py           Textual events → libghostty KeyEvent
  widget.py         TerminalView(Widget)
  py.typed
```

The library never owns a process, PTY, or transport. Bytes in, bytes out.

## 2. Verified ABI facts

All measured against `pyghostty==0.1.0`, macOS arm64, unless noted.

| Fact | Status |
|---|---|
| Render-state symbols present in `_cdef.py` | **Confirmed** — 21 symbols |
| Real call flow | `render_state_new` → `update` / `begin_update` / `end_update` → `row_iterator_new`/`_next` → `row_cells_new`/`_next`/`_select` |
| Dirty tracking granularity | **Global + per-row**, not per-cell |
| `WRITE_PTY` callback delivers query replies | **Confirmed** — `hello\x1b[6n` → `b'\x1b[1;6R'` |
| Automatic replies with only `WRITE_PTY` set | DSR 6 → `b'\x1b[1;1R'`, DA1 → `b'\x1b[?62;22c'`, XTVERSION → `b'\x1bP>|libghostty\x1b\\'` |
| **Silent** with only `WRITE_PTY` set | `CSI 14t`, `CSI 16t`, `CSI 18t`, `OSC 11 ?`, ENQ |
| With `OPT_SIZE` registered (rows/cols cached, cell dims `0`) | `CSI 18t` → `b'\x1b[8;24;80t'`, `CSI 14t` → `b'\x1b[4;0;0t'`, `CSI 16t` → `b'\x1b[6;0;0t'` |
| `GhosttySizeReportSize` fields | `rows`, `columns`, `cell_width`, `cell_height` — **no pixel fields**; pixel size is derived |
| `ghostty_terminal_resize(..., 0, 0)` | **Succeeds**; `WIDTH_PX`/`HEIGHT_PX` become `0` |
| `ghostty_terminal_reset()` clears | title, scrollback (36→0), total rows (41→5), alternate-screen state |
| `ghostty_terminal_reset()` **preserves** | OSC 10/11 default-colour overrides — `0x0000ff` before and after |
| Colour accessors on a fresh terminal | return `rc=-4`; readable only once an override exists |
| Encoders present | `ghostty_key_encoder_*` incl. `setopt_from_terminal`, `ghostty_focus_encode`, `ghostty_paste_is_safe`, `ghostty_paste_encode` |
| Resource-limit options present | `KITTY_IMAGE_STORAGE_LIMIT`, `APC_MAX_BYTES`, `APC_MAX_BYTES_KITTY` |
| Cell and row representation | `GhosttyCell` and `GhosttyRow` are opaque `uint64_t` handles; cell data is read through `ghostty_cell_get`/`ghostty_cell_get_multi` |
| Public cell metadata | `GhosttyCellWide` supplies `NARROW`, `WIDE`, `SPACER_HEAD`, and `SPACER_TAIL`; `HAS_STYLING`, `STYLE_ID`, and `HAS_HYPERLINK` cover the remaining render decisions |
| Wheel tags | `manylinux_2_27_aarch64.manylinux_2_28_aarch64`, `manylinux_2_27_x86_64.manylinux_2_28_x86_64`, `macosx_13_0_arm64`, `macosx_13_0_x86_64` |

### v1 errors corrected

- v1 named `ghostty_render_state_begin()` and `ghostty_render_state_next_cell()`.
  **Neither exists.** They came from a search-result paraphrase, not the header.
- v1 said the render API reports "only the cells that actually changed." It
  reports **dirty rows**. A keystroke dirties a row, not three cells.
- v1 claimed the existing `pysshmanager` suite is a backend-neutral migration
  harness (quoting ADR-0001). **False** — see §8.

### The scrolling blocker

`ghostty_terminal_scroll_viewport(GhosttyTerminal, GhosttyTerminalScrollViewport)`
passes a **tagged union by value**, which CFFI ABI mode cannot call.

`pyghostty._ffi` already solves this shape for `GhosttyPoint` with a
layout-identical struct twin plus a `union_fn()` caster, and its own comment
names `GhosttyTerminalScrollViewport` as needing the same treatment "later."
That twin is not in 0.1.0.

The union is `{intptr_t delta | size_t row | uint64_t _padding[2]}` = 16 bytes,
so the struct is tag(4) + pad(4) + 16 = 24 bytes — over the 16-byte threshold,
therefore passed indirectly on both arm64 AAPCS64 and x86-64 SysV. A twin is
sound:

```c
typedef struct { uint64_t a; uint64_t b; } GhosttyTerminalScrollViewportValueS;
typedef struct { GhosttyTerminalScrollViewportTag tag;
                 GhosttyTerminalScrollViewportValueS value; } GhosttyTerminalScrollViewportS;
```

Assert `sizeof`/`alignof` against the real type at import, exactly as `_ffi.py`
does for `GhosttyPointS`, and verify on all four target platforms. Upstream it to
`pyghostty`, but **do not block on an upstream release** — carry it in
`_native.py`.

### Not verified

- Bundled Ghostty commit `88b4cd0` — could not confirm from the shipped
  `.dylib`. Inconsequential, but record whatever the loader can actually read.
- Whether `reset()` clears a *selection*. Setting one requires the selection API
  rather than VT input, and the question is moot given the `hard_reset()`
  decision in §3.5 — recreation clears everything. Asserted anyway in
  `test_reset.py` for documentation value.
- The review's benchmark figures (38.6 ms per-cell vs 4.1 ms render-state at
  120×40). Not reproduced. They support this design rather than challenge it, so
  they are treated as motivation, not evidence — §11 requires our own numbers.

## 3. `emulator.py` — the `Terminal` contract

```python
class Terminal:
    def __init__(
        self,
        cols: int,
        rows: int,
        *,
        scrollback: int = 5000,
        theme: TerminalTheme | None = None,
        clipboard: ClipboardPolicy = ClipboardPolicy(),
        limits: ResourceLimits = ResourceLimits(),
    ) -> None: ...

    def feed(self, data: bytes) -> TerminalEffects: ...
    def snapshot(self, *, force: bool = False) -> Frame | None: ...
    def resize(self, cols: int, rows: int) -> None: ...
    def scroll_viewport(self, request: ScrollRequest) -> None: ...
    def set_theme(self, theme: TerminalTheme) -> None: ...
    def hard_reset(self) -> None: ...
    def close(self) -> None: ...

    def encode_key(self, event: KeyEvent) -> bytes | None: ...
    def encode_mouse(self, event: MouseEvent) -> bytes | None: ...
    def encode_focus(self, focused: bool) -> bytes | None: ...
    def encode_paste(self, text: str) -> bytes | None: ...

    modes: TerminalModes
    title: str | None
    viewport: ViewportState
    closed: bool
```

### 3.1 `feed()` and `snapshot()` are separate

v1's single `feed() -> FeedResult` conflated four jobs and could not describe a
frame produced by anything other than a feed — a resize, reset, viewport scroll,
or theme change would have had no way to report its own repaint.

```python
@dataclass(frozen=True, slots=True)
class TerminalEffects:
    pty_writes: tuple[bytes, ...]                 # ordered
    notifications: tuple[TerminalNotification, ...]

@dataclass(frozen=True, slots=True)
class Frame:
    generation: int
    cols: int
    rows: int
    full_redraw: bool
    row_patches: tuple[RowPatch, ...]
    styles: tuple[CellStyle, ...]   # index == style_id; complete generation table
    links: tuple[str, ...]          # index == link_id; complete generation table
    cursor: CursorState
    viewport: ViewportState
    frame_pending: bool
```

`feed()` mutates and returns effects. `resize()`, `hard_reset()`,
`scroll_viewport()`, and `set_theme()` mutate and return nothing. `snapshot()`
owns the entire render-state update and dirty-flag lifecycle, and returns `None`
when nothing changed. `force=True` produces a full frame regardless.

**Dirty-flag discipline:** global and per-row dirty state are cleared
independently; clearing one does not clear the other. `snapshot()` clears both,
and only after it has copied everything it needs.

**No borrowed C memory escapes.** `Frame`, its rows, and its cells are immutable
Python copies. No public object holds a grid reference past the next mutation.
Because `snapshot()` clears dirty state before returning, a Python exception
while the widget applies a frame loses it — recovery is `snapshot(force=True)`,
and this is documented on the method.

**Frames are self-contained.** Every frame carries the complete style and link
tables for its generation, not ids that require a later lookup against mutable
terminal state. The widget applies the frame, both tables, and the shadow-buffer
patches as one assignment. A generation rollover therefore cannot expose a
shadow buffer whose ids resolve against the wrong tables.

**Synchronized output (DECSET 2026) is not reimplemented.** First measure how
Ghostty's own dirty flags behave inside a synchronized frame, including an
unterminated one. Only if measurement shows the library does not already gate
updates do we add withholding on top. `frame_pending` exists to carry that state
if needed. An unterminated synchronized frame must never freeze the widget
indefinitely — a bounded timeout forces a snapshot.

### 3.2 Terminal-generated PTY writes

`GHOSTTY_TERMINAL_OPT_WRITE_PTY` is configured at construction. The C callback
fires **synchronously inside `ghostty_terminal_vt_write`**, so it does exactly
one thing: copy bytes into an instance-owned list. It never calls back into
`vt_write`, never awaits, never blocks. `feed()` drains the list after
`vt_write` returns and hands it back as `TerminalEffects.pty_writes`.

This closes ADR-0001's "no terminal query responses" gap. Verified: `\x1b[6n` →
`b'\x1b[1;6R'`.

**Callback safety.** Every CFFI callback object is held on the `Terminal`
instance until after `ghostty_terminal_free`. No exception may escape a
callback: it is captured and re-raised from the enclosing Python operation once
`vt_write` returns.

**Terminal identity is documented, not configurable.** v1's review proposed a
`TerminalProfile` with settable `term`, `xtversion`, and `device_attributes`.
That is more surface than any consumer needs — libghostty already answers DA1 as
`\x1b[?62;22c`. v1 of this library instead:

- pins the default responses with tests, so a libghostty upgrade that changes
  terminal identity fails loudly. Measured: DSR 6 → `\x1b[1;1R`, DA1 →
  `\x1b[?62;22c`, XTVERSION → `\x1bP>|libghostty\x1b\\`. Note that XTVERSION
  self-identifies as `libghostty` and is not configurable — further evidence
  that a settable identity surface is not worth building;
- registers `OPT_SIZE` so XTWINOPS answers (§3.4);
- documents which callbacks are deliberately left unconfigured: **ENQUIRY** and
  **colour-scheme / `OSC 11 ?`**, both measured silent. A consumer that needs
  them can be served in a later version;
- exposes only the two things that are genuinely policy:

```python
@dataclass(frozen=True, slots=True)
class ClipboardPolicy:
    allow_write: bool = False           # OSC 52 off by default
    max_bytes: int = 1_000_000

@dataclass(frozen=True, slots=True)
class ResourceLimits:
    # Bounds historical accumulation. The effective per-terminal limit is
    # max(this value, cols * rows), recomputed on construction and resize.
    max_interned_styles: int = 4096
    max_interned_links: int = 1024
    max_link_uri_bytes: int = 2048
    kitty_image_storage_bytes: int = 0  # graphics parsed but not stored
    apc_max_bytes: int = 8192
    apc_max_bytes_kitty: int = 8192
    notification_rate_per_sec: int = 60
```

`max_interned_styles` limits styles retained after their cells have left the
viewport; it does not cap the fidelity of one visible frame. A full extraction
can visit `cols * rows` cells and therefore require that many distinct styles.
The effective limit is `max(configured, cols * rows)`, applied at construction
and after every resize. This is the termination invariant for rollover: after a
style overflow, a forced full extraction is guaranteed to fit.

Links deliberately have different overflow semantics. An over-long URI or a
full link table yields `link_id=None`; it never raises and never fails a frame.
Links are optional metadata, so rendering unlinked content is safer than making
terminal output unavailable. APC limits are passed to Ghostty alongside the
zero kitty-image storage limit.

Configurable identity is added when a consumer needs to change it.

### 3.3 Viewport, not a history index

v1 proposed a widget-side history-row cache keyed by distance from the newest
row, and rationalised the resulting drift as acceptable. It is not: a real
terminal keeps scrolled content anchored while output continues. That
rationalisation is withdrawn.

Scrollback is Ghostty's job:

```python
@dataclass(frozen=True, slots=True)
class ViewportState:
    at_bottom: bool
    offset: int
    scrollback_rows: int
    total_rows: int

class ScrollRequest:  # top | bottom | delta(n) | row(n)
    ...
```

`SCROLLBACK_ROWS`, `TOTAL_ROWS`, and `VIEWPORT_ACTIVE` are all available through
`ghostty_terminal_get`. The widget's shadow buffer is *always the current
viewport* and never needs a second cache. Page-up, wheel, and page-down mutate
Ghostty's viewport and request a new frame.

This depends on the union twin from §2.

### 3.4 Sizing

`resize(cols, rows)` calls `ghostty_terminal_resize(t, cols, rows, 0, 0)`.
Measured: passing `0` for both pixel dimensions succeeds and sets `WIDTH_PX`/
`HEIGHT_PX` to `0`. A Textual cell renderer cannot know physical pixels, so
reporting zero is the honest answer. Inventing `8×16` — as `pyghostty.core`
does — would make the terminal lie.

**Correction to this document's first draft.** It claimed XTWINOPS pixel queries
"will reflect" the zero dimensions. They do not: with only `WRITE_PTY`
configured, `CSI 14t`, `CSI 16t`, and `CSI 18t` produce **no reply at all**,
because the size report is driven by a separate callback. Passing zero to
`resize()` does not by itself make anything answer.

`GHOSTTY_TERMINAL_OPT_SIZE` is therefore registered, returning the cached
`rows`/`columns` with `cell_width = cell_height = 0`. Measured result:

| Query | Reply |
|---|---|
| `CSI 18t` | `b'\x1b[8;24;80t'` — text area in characters |
| `CSI 14t` | `b'\x1b[4;0;0t'` — text area in pixels, honestly zero |
| `CSI 16t` | `b'\x1b[6;0;0t'` — cell size in pixels, honestly zero |

`GhosttySizeReportSize` has no pixel fields at all (`rows`, `columns`,
`cell_width`, `cell_height`), so zero cell dimensions *are* the mechanism for
reporting unknown pixel size.

`OSC 11 ?` (colour query) and ENQ remain unanswered; both need their own
callbacks and neither is configured in v1. This is documented behaviour, not an
oversight — see §3.2.

**Argument order is `(cols, rows)` throughout this library.** `pysshmanager`'s
`SshSession.resize` and `PtyProcess.resize` are both `(rows, cols)`. The
migration must not silently transpose them; §8 makes the adapter explicit.

### 3.5 Lifecycle, ownership, threading

- **Constructed by `TerminalView` → closed by `TerminalView`.**
- **Injected → borrowed, never closed by the widget**, unless
  `close_terminal=True` transfers ownership explicitly.
- `close()` is idempotent. Every public method raises `RuntimeError` after close.
- **No `__del__`.** v1 proposed one; with CFFI callback cycles it is a hazard,
  not a safety net. Use explicit `close()`, context management, and at most a
  `weakref.finalize` leak guard.
- **Thread-confined.** `libghostty-vt` is not thread-safe. Construction, feed,
  snapshot, resize, scroll, encode, and close must all occur on the owning
  thread/event loop; callers marshal bytes arriving from elsewhere. A DEBUG-only
  owner-thread assertion enforces it.

**`hard_reset()` frees and recreates. This is decided, not conditional.**

`ghostty_terminal_reset()` clears title, scrollback, total rows, and
alternate-screen state — but **measurement shows it preserves OSC 10/11
default-colour overrides**: after `\x1b]10;#ff0000\x07`, `COLOR_FOREGROUND` reads
`0x0000ff` both before and after the reset.

That is disqualifying for reconnect. A remote `.bashrc` that repaints the
terminal would leave its colours bleeding into the next session, and colour
overrides are precisely the kind of state a user attributes to the *client*
rather than the dead connection.

`hard_reset()` therefore frees and recreates the terminal, render state,
iterators, encoders, and callbacks, carrying the retained configuration
(`scrollback`, `theme`, `clipboard`, `limits`) forward. The cost is one
allocation per reconnect, which is nothing against spawning an SSH process.

`ghostty_terminal_reset()` is still the right call for a guest-initiated RIS
(`ESC c`), where preserving embedder colour configuration is correct behaviour.
The two are different operations and the spec keeps them distinct.

### 3.6 Errors are fatal and visible

v1 specified that a binding exception during `feed()` be caught, logged at
DEBUG, and feeding continued. That is `pysshmanager`'s pyte `_pump` guard
reproduced inside its replacement, and it is withdrawn.

`ghostty_terminal_vt_write()` is deliberately non-failing for untrusted input —
malformed VT bytes are libghostty's problem and never surface as a Python
exception. Therefore a Python or CFFI exception means a wrapper bug, an ABI
mismatch, a lifecycle violation, or corrupted state. Continuing is how you get a
silently frozen pane.

| Condition | Policy |
|---|---|
| Malformed remote bytes | Handled by libghostty; not an error |
| `GHOSTTY_TERMINAL_DATA_VT_PROCESSING_ERROR` | Exposed as diagnostics; **not** fatal — untrusted input must not crash the pane |
| Non-success `GhosttyResult` | Raise typed `GhosttyError` |
| Python/CFFI wrapper exception | Raise typed `GhosttyError` |
| Fatal error reaches the widget | Explicit terminal-failed state + notification. Never render stale content as live |

## 4. `cells.py`

```python
@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    width: Literal[0, 1, 2]      # 0 = continuation of a wide grapheme
    style_id: int
    link_id: int | None
```

v1's `Cell(text, style_id, link_id)` could not distinguish an empty cell from a
wide-grapheme continuation. `width` fixes that and drives both rendering and
selection.

`CellStyle` covers default/palette/RGB foreground and background, bold, faint,
italic, blink, inverse, invisible, strike, overline, underline kind, and
underline colour.

**Style ids carry resolved colours**, taken from the render state's resolved
cell colours, so an OSC palette change naturally produces different intern keys
rather than requiring separate invalidation bookkeeping. A default-colour or
theme change still forces `snapshot(force=True)` and rebuilds the Rich style map.

**Interning is bounded** (`ResourceLimits`). A hostile remote can emit unbounded
true-colour combinations or OSC 8 URIs; a process-lifetime dictionary would be a
memory sink. Style insertion raises `InternerFull` *before* exceeding the
effective limit. `snapshot()` catches it, discards the partial extraction,
cycles the generation, and restarts with a forced full frame. The effective
style limit is always at least `cols * rows`, so the second extraction cannot
overflow.

Link insertion never raises. It returns `None` when the URI is too long or the
link table is full, and the cell is rendered without a link. A style is
load-bearing render data; a hyperlink is optional metadata, so the two
interners intentionally do not share failure policy.

**Rollover is not a quiet eviction.** Every `style_id` and `link_id` already
sitting in the shadow buffer refers to the *current* generation's tables. Cycling
those tables invalidates them all, and a partially-updated shadow buffer would
render with ids that no longer resolve. Rollover is therefore an atomic,
all-or-nothing operation:

1. increment the intern generation and clear both tables;
2. restart extraction with `snapshot(force=True)` — a complete frame, not a
   patch set;
3. swap the shadow buffer *and* both frame-owned lookup tables together, so no render can
   observe a shadow buffer and a table from different generations.

`Frame.generation` carries the intern generation, and the widget asserts it
matches before applying row patches; a mismatch means a full frame is required
and any patch set is discarded. This is the same guard that makes recovery from
a dropped frame (§3.1) safe.

Also specified: spacer head/tail cells, combining graphemes, tabs, invalid
UTF-8, and over-wide graphemes each get a defined mapping into the fixed grid.

**Never decode a private cell bit layout.** `GhosttyCell` and `GhosttyRow` are
opaque `uint64_t` handles. Rendering uses the public row/cell iterators plus
`ghostty_cell_get`/`ghostty_cell_get_multi`, including `GhosttyCellWide` for
continuations. A private fast path is considered only after a measured public-
path budget miss and must be guarded by differential tests against the public
accessors.

## 5. `theme.py`

v1 omitted this entirely. Without it, default cells, reverse video, OSC colour
overrides, and cursor/selection rendering can disagree with the widget's own
background.

`TerminalTheme` carries default foreground, background, cursor colour, and the
256-colour palette, and is pushed into Ghostty via `GHOSTTY_TERMINAL_OPT_COLOR_*`.
`set_theme()` forces a full snapshot and rebuilds Rich styles.

Precedence, in order: embedder defaults → terminal OSC overrides → selection
overlay → cursor overlay.

## 6. `widget.py` — `TerminalView`

```python
class TerminalView(Widget):
    def __init__(
        self,
        *,
        send: Callable[[bytes], Awaitable[None]],
        resize_transport: Callable[[int, int], None] | None = None,
        scrollback: int = 5000,
        theme: TerminalTheme | None = None,
        reserved_keys: frozenset[str] = frozenset(),
        terminal: Terminal | None = None,
        close_terminal: bool = False,
    ) -> None: ...

    def feed(self, data: bytes) -> None
    def hard_reset(self) -> None          # terminal and shadow state only
    async def reset_io(self) -> None      # writer/session boundary
    def sync_terminal_size(self) -> tuple[int, int]

    class Resized(Message): cols: int; rows: int
    class TitleChanged(Message): title: str
    class Bell(Message): ...
    class LinkClicked(Message): uri: str
    class ClipboardWrite(Message): text: str
    class TerminalFailed(Message): error: GhosttyError
```

### 6.1 Deterministic update sequence

1. `effects = terminal.feed(data)`
2. enqueue `effects.pty_writes` in order; post notification messages
3. `frame = terminal.snapshot()`
4. apply the frame to the shadow buffer atomically
5. refresh only the changed Textual rows

### 6.2 One ordered output path

v1 said the keystroke path "should not queue." That is wrong: PTY writes are
generated *synchronously during `feed()`*, so keys, paste, focus reports, and
query replies must share a single FIFO or their relative order is
nondeterministic — a DSR reply interleaved into a keystroke corrupts the input
stream.

`TerminalView` owns one bounded `asyncio.Queue` and a writer task that calls
`send` in order.

The review offered an alternative — make `feed()` async and let the consumer
await writes. Rejected: that couples PTY draining to SSH write latency, so a slow
write stalls *rendering*, not just input. Queueing a few bytes costs nothing
against an SSH round trip.

**Overflow is fail-fast, not backpressure.** `feed()` is synchronous and cannot
await a full queue, so calling this backpressure would be a misnomer that hides a
real decision. Two enqueue paths, one FIFO:

| Caller | Path | On full queue |
|---|---|---|
| `feed()` → `pty_writes` (sync) | `put_nowait()` | `QueueFull` → `TerminalFailed`, queue shut down |
| `on_key` / `on_paste` / focus (async) | `await put()` | waits, applying genuine backpressure to input |

Both enqueue into the same FIFO, so relative ordering holds regardless of which
path a given byte arrived through. A full queue means the transport has stalled
badly enough that silently dropping a query reply would desynchronise the remote
application — failing loudly is correct.

Also defined: `send` raising → `TerminalFailed` and queue shutdown;
drain-then-cancel on close; input before mount is rejected, not buffered.

### 6.3 Sizing contract — no first-resize race

Today `TerminalWidget.on_mount` calls `session.resize()` **synchronously**, which
is what guarantees SSH starts at the real pane size. Replacing that with a posted
`Resized` message would let `SessionTabs.add_session()` return and spawn SSH at
80×24.

Both paths are therefore provided:

- `resize_transport(cols, rows)` — a **synchronous** callback invoked during
  `on_mount`/`on_resize`, before any message is posted;
- `Resized` — posted afterwards for observers;
- `sync_terminal_size()` — public, callable by a consumer that would rather pull
  than be pushed.

Resize events are coalesced; zero dimensions are rejected.

### 6.4 Rendering

Shadow buffer holds the current viewport. `render_line(y)` reads it with **zero
FFI**, groups runs by `style_id`, applies the selection span and cursor overlay,
and ends with `.apply_offsets(0, y)` — load-bearing for click-to-cell mapping,
with a regression test, per ADR-0002.

### 6.5 Cursor

Taken from the render state's **viewport** cursor fields, not active-screen
coordinates, so it hides correctly when scrolled away and handles wide-tail
state. Block → reverse; underline → underline; bar → reverse (documented
limitation: a sub-cell bar is not representable, so bar and block look alike).
Blink is a widget timer, off when unfocused, invalidating only the cursor row.

### 6.6 Selection

v1 semantics: Textual supplies the endpoints, extraction reads the immutable
viewport shadow. Ghostty's native selection API is *not* also authoritative —
one owner only. Trailing padding trimmed, wide continuation cells skipped, hard
breaks joined with `\n`, display wraps preserved. Endpoint translation to Ghostty
grid refs, for true soft-wrap copy semantics, is deferred.

### 6.7 Keys and paste

`ghostty_key_encoder_setopt_from_terminal()` is called **before every encode**, so
DECCKM, keypad mode, modifyOtherKeys, Kitty flags, and backarrow mode always
reflect current state. `encode_key` returns `bytes | None`.

`reserved_keys` is checked *before* encoding, and the event is left unstopped so
priority app bindings still fire.

Paste uses `ghostty_paste_is_safe()`; unsafe unbracketed pastes containing
control characters are rejected with a notification rather than sent.

### 6.8 Mouse

v1 of the widget ships **`MouseMode.LOCAL` only**: wheel scrolls the viewport,
drag selects, nothing is sent to the child. ADR-0002 holds by construction.

`Terminal.encode_mouse()` is implemented and tested headlessly so the encoder is
ready, but no widget-level remote capture, motion coalescing, or protocol-mode
handling exists in v1. Remote mouse is deferred to v0.2, where it must also
define a modifier that forces local selection and resolve the conflict with OSC 8
click activation.

### 6.9 Failure containment — the widget is the last boundary

§3.6 makes emulator errors fatal and typed. That is only safe if something stops
them at the widget. **`TerminalView.feed()` catches `GhosttyError`** and never
re-raises.

This is not defensive habit — it closes a specific regression path. If a
`GhosttyError` escaped `view.feed()`, it would propagate into
`SshSession._pump`, past the read loop, into its `finally`, where
`await proc.wait()` blocks until the child exits. That is *exactly* the mechanism
that froze a pane in ADR-0001: the process keeps running, the session stays
`CONNECTED`, and the screen never updates again. Removing pyte's `_pump` guard
without catching at the widget would reintroduce the bug the guard was hiding.

On a fatal emulator error the view:

1. transitions to a **failed** state;
2. posts `TerminalFailed(error)`;
3. **ignores all subsequent `feed()` calls** — no partial rendering, no stale
   content presented as live;
4. shuts down the writer queue.

The failed state is visible in the UI. A pane that has stopped emulating must
never look like a pane that has stopped receiving output.

### 6.10 Reset and reconnect discard pending writes

Terminal reset and transport reset are separate operations. `hard_reset()`
recreates the emulator and clears the shadow buffer synchronously, but it cannot
cancel an in-flight `await send(...)`. `reset_io()` is the asynchronous session
boundary.

Bytes still queued from the previous session (a keystroke, a DSR reply generated
just before the disconnect) must never reach the reconnected process, where they
would be interpreted by a different shell.

`reset_io()` therefore:

- increments the writer generation;
- cancels and **awaits** the writer task, so an in-flight send cannot race the
  next transport;
- drains and discards the old queue without sending;
- creates a fresh queue/writer and clears the failed state.

`hard_reset()` then recreates the terminal, resets the shadow buffer, and forces
`snapshot(force=True)`. `on_unmount` also awaits `reset_io()` before releasing
the widget.

Ordering for `pysshmanager` reconnect is fixed in §8: close process →
`await view.reset_io()` → `view.hard_reset()` → start new process.

### 6.11 Link and clipboard security

OSC content is untrusted remote input.

- `LinkClicked` reports a URI and nothing else. **The library never opens it.**
- URI length and interned-link count are bounded (`ResourceLimits`); excess
  links degrade to plain, unlinked cells rather than failing rendering.
- Scheme policy belongs to the application, not the renderer.
- OSC 52 clipboard write is **off by default**, size-bounded, text only.
- Bell, title, and clipboard notifications are rate-limited against UI floods.
- Kitty image storage limit is `0` in v1: graphics are parsed but not retained,
  so deferring rendering does not create memory exposure.

## 7. `_native.py`

- Exact pin: `pyghostty==0.1.0`. v1 contradicted itself with `>=0.1` in one
  section and "pin exactly" in another.
- Startup verification of required symbols and expected struct sizes/alignments,
  including both union twins.
- **`GhosttyUnavailable` is raised when constructing `Terminal`, not at import.**
  Lazy loading keeps docs, type checking, packaging metadata, and apps with
  optional terminal features importable on unsupported platforms. The error
  carries the running platform and the supported wheel matrix.
- `PYGHOSTTY_LIB` can point at an arbitrary, ABI-incompatible library. Documented
  as unsupported; the layout checks run regardless and fail loudly.
- `pyghostty._ffi` and `._cdef` are **private modules**. A nominally compatible
  release may change them without deprecation — hence the pin plus the checks.

```toml
dependencies = [
  "textual>=8.2.8,<9",
  "pyghostty==0.1.0",
]
```

## 8. `pysshmanager` migration

1. `SshSession.on_output` becomes `Callable[[bytes], None]` receiving the raw
   chunk. `_pump` stops decoding to `str` entirely.
2. `SshSession` loses `screen`, `stream`, `_init_screen`, `terminal_modes`, and
   the `_pump` try/except. It becomes process and transport only.
3. The output callback is attached **before** SSH starts; the UI already mounts
   the terminal first, so preserve that order.
4. The wrapper passes `resize_transport=lambda cols, rows: session.resize(rows, cols)`
   — note the deliberate transposition, in one place, commented.
5. `TerminalEffects.pty_writes` route through the same ordered path as keys and
   paste, i.e. `SshSession.send`.
6. `reconnect()` no longer resets a screen it does not own. Order becomes:
   close process → `await view.reset_io()` → `view.hard_reset()` → start new
   process. The wrapper drives it.
7. The output callback is detached before unmount; a final PTY chunk racing pane
   removal is dropped, explicitly.
8. `_APP_KEYS` moves into `reserved_keys`; `Ctrl+]` and app bindings stay in the
   thin wrapper.
9. Remote mouse stays off regardless of what the child requests.

### The test suite is not a free harness

v1 claimed, quoting ADR-0001, that the existing tests assert behaviour rather
than pyte internals. Verified false:

| Test | Coupling |
|---|---|
| `tests/ssh/test_session.py` | reads `session.screen.display` (4 sites); monkeypatches `session.stream` |
| `tests/ui/test_terminal.py` | calls `session.stream.feed()`; reads `terminal._get_lines()` |
| `tests/ssh/test_terminal_protocol.py` | hardcodes `mode << 5`, pyte's private-mode shift |

These are rewritten around public behaviour, not inherited. **Budget roughly one
additional focused day**, allocated as:

- `SshSession` tests become transport, lifecycle, and resize tests over a raw
  bytes callback — no emulator state at all.
- Emulator, ANSI style, mode, query, and reset assertions move into
  `ghostty-textual`'s suite, where they belong.
- UI tests feed `TerminalView` directly and keep their real subjects: mouse
  policy, selection, focus, sizing, and shortcuts.
- `test_session.py`'s "pump survives a parser exception" test — which asserts
  pyte's failure is swallowed — inverts. It becomes a test that a fatal emulator
  error produces a **visible** terminal failure (§6.9). The old test encoded the
  behaviour this project exists to remove.

This is bounded and well-understood, not open-ended. It is still the largest
under-estimate in v1 and should be scheduled rather than absorbed.

## 9. Test matrix

The probes written while producing this spec become the first four test modules.
They are converted immediately — scratch scripts are not preserved as scripts,
because an assertion that nobody runs is not evidence.

| Module | Covers | Already measured |
|---|---|---|
| `test_abi.py` | union size/alignment for both twins, twin invocation, required symbols | scroll-viewport union is 24 bytes, passed indirectly |
| `test_effects.py` | DSR, DA1–DA3, XTVERSION, size reports, colour-scheme and ENQ policy | DSR/DA1/XTVERSION automatic; `OSC 11 ?` and ENQ silent |
| `test_reset.py` | title, history, alternate screen, palette, selection | title/scrollback/total/alt-screen cleared; palette and selection **open** |
| `test_resize.py` | zero pixel dimensions, XTWINOPS size reports | `resize(...,0,0)` succeeds; `CSI 18t` → `\x1b[8;24;80t` with `OPT_SIZE` |


**Native lifecycle** — double close; every public op after close; close with
effects pending; `hard_reset` clears history/modes/title/palette/selection/alt
screen; callbacks survive GC until close; create/feed/close loop under a leak
checker.

**Effects and identity** — DSR written to PTY; DA1/DA2/DA3, XTVERSION, size,
colour-scheme, enquiry policy pinned; ordering when one feed yields title + bell
+ PTY writes; OSC 52 default-off and payload limits; a callback exception becomes
a fatal Python error after `vt_write` returns.

**Rendering** — full/partial/clean dirty states; both dirty flags acknowledged;
cursor moving rows invalidates old and new; theme and OSC palette changes
repaint; default/reverse/256/true-colour; every underline kind and colour;
combining graphemes, ZWJ emoji, ambiguous width, wide char at the right margin;
synchronized output including a missing terminator.

**Scrollback and selection** — content stays anchored while output arrives;
bottom-follow resumes; ring eviction; resize/reflow while scrolled (anchor rule:
keep the top visible row's content, clamp at bounds); primary/alternate
transitions; hard vs soft break copy; wide-tail endpoints; selection across empty
rows.

**Input** — DECCKM normal and application; keypad; modifyOtherKeys; Kitty flags;
ctrl/alt/shift combinations; bracketed, unbracketed, and unsafe paste; focus
events on and off; reserved keys never encoded and never stopped.

**Compatibility** — `CSI ? 4 m` parses cleanly (the sequence that froze pyte);
vim, htop, and tmux capture replay; UTF-8 split across feeds
(`b'\xe2\x94'` + `b'\x80'` → `─`).

**Packaging** — build, install clean, import, construct, feed, close; macOS
arm64/x86_64 and glibc Linux arm64/x86_64 smoke jobs; unsupported-platform error;
missing/corrupt library error; symbol and ABI-layout checks.

**Fuzz** — random and malformed streams **in subprocesses with timeouts**. A
segfault cannot be reported from inside the pytest process.

## 10. Performance budgets

Committed to the repo as a runnable script, not left as prose:

- 120×40 full-frame extraction: **p95 < 10 ms** on the macOS CI runner.
- Single-dirty-row update: separate budget, measured, no regression gate until a
  baseline exists.
- Steady-state memory across a long session with bounded interning.

The review reports 38.6 ms for per-cell reads versus 4.1 ms for render-state
iteration at 120×40. Those are the reviewer's numbers, not ours; the budget is
set from our own first measurement.

## 11. Risks

| Risk | Mitigation |
|---|---|
| Scroll-viewport union twin wrong on some ABI | Size/alignment assertions at import; all four platforms in CI; the `GhosttyPointS` precedent works |
| `libghostty-vt` API churn (promised) | One boundary (`emulator.py` + `_native.py`); exact pin; scheduled CI against latest |
| `pyghostty` abandoned (3 days old, 3 stars) | Apache-2.0/MIT, vendorable; escape hatch is `ziglang` + `cibuildwheel`, which is how pyghostty itself builds |
| Private `_ffi`/`_cdef` change silently | Exact pin plus symbol and layout verification |
| v1 scope | §12 orders the work so parity lands before features |
| Migration test rewrite under-estimated | Called out in §8; sized before cutover |

## 12. Implementation order

1. **Native loader and lifecycle** — pin, compatibility checks, both union twins,
   terminal + render-state allocation, idempotent close, callback retention.
   Lands `test_abi.py`.
2. **Effects** — WRITE_PTY plumbing; register `OPT_SIZE`; pin terminal identity
   with tests. Lands `test_effects.py`, `test_reset.py`, `test_resize.py`, and
   answers the two open `reset()` questions (palette overrides, selection).
3. **Frame extraction** — immutable cells/styles/cursor via render state, dirty
   acknowledgement, benchmark script.
4. **Viewport** — scroll shim, anchoring tests, resize/reflow.
5. **Minimal widget** — shadow viewport, row rendering, sizing contract, ordered
   send path, failure containment (§6.9) and reset-discards-queue (§6.10).
6. **Parity** — local scroll, selection, keys, paste, focus, cursor.
7. **Features and security** — title, bell, OSC 8, clipboard policy, limits.
8. **`pysshmanager` cutover** — transport callback, initial sizing, reconnect,
   wrapper bindings, test rewrite, pyte deletion.
9. **v0.2** — remote mouse, `pty.py`, Ghostty-native selection.

## 13. Cutover checklist

- [ ] Render-state API used; no per-cell `style()` loop
- [ ] PTY query responses delivered in order
- [ ] Scrolled content stays anchored while output arrives
- [ ] `hard_reset` produces a verifiably clean terminal
- [ ] Deterministic ownership for every native object and callback
- [ ] Binding/ABI failures fatal and visible
- [ ] A fatal emulator error never escapes `TerminalView.feed()` into `SshSession._pump`
- [ ] `reset_io()` cancels the writer and discards the previous session's queued writes
- [ ] `hard_reset()` recreates terminal and shadow state after I/O reset
- [ ] Intern rollover forces a full frame; no shadow buffer renders stale ids
- [ ] Exact `pyghostty` version and ABI verified at startup
- [ ] Initial PTY size applied synchronously before SSH starts
- [ ] Local mouse, selection, scrollback policy preserved
- [ ] Performance and memory budgets pass
- [ ] vim, htop, tmux replay and interactive smoke pass
- [ ] No `pyte` import or compensation path anywhere in `pysshmanager`

## 14. Success criteria

Line counts are deliberately **not** a criterion — v1 used "~500 lines deleted",
which pressures the implementation toward dropping error handling to hit a
number.

1. No runtime `pyte` dependency or import in `pysshmanager`.
2. All pyte compensation modules removed.
3. Parity behaviours demonstrated by test: scrollback, drag-select, alt screen,
   titles, wide chars, `Shift+PageUp/Down`, wheel, `Ctrl+]`.
4. Capabilities gained and tested: DECCKM, Kitty keyboard, query replies, OSC 8,
   cursor shapes, synchronized output.
5. Performance and memory budgets met (§10).
6. Clean install-and-import on all four supported wheel platforms.

## 15. Review disposition

| Item | Disposition |
|---|---|
| B1 separate effects from rendering | Accepted (§3.1) |
| B2 surface PTY writes | Accepted (§3.2); `TerminalProfile` **scoped down** to `ClipboardPolicy` + `ResourceLimits` + pinned defaults |
| B3 use Ghostty's viewport | Accepted (§3.3); v1's drift rationalisation withdrawn |
| B4 ownership/lifecycle/threading | Accepted (§3.5); `reset()` behaviour measured rather than deferred |
| B5 do not swallow binding errors | Accepted (§3.6) |
| B6 pin the ABI | Accepted (§7) |
| I1 cell/style model | Accepted (§4) |
| I2 theme API | Accepted (§5) |
| I3 one ordered output path | Accepted (§6.2); **async-`feed()` alternative rejected** with reasoning |
| I4 first-resize race | Accepted (§6.3); pixel-dimension question **measured**, answer is `0` |
| I5 selection semantics | Accepted (§6.6) |
| I6 key/paste explicitness | Accepted (§6.7) |
| I7 reduce mouse scope | Accepted (§6.8) — headless encoder in v1, widget `REMOTE` deferred |
| I8 link/clipboard security | Accepted (§6.11) |
| I9 cursor from render state | Accepted (§6.5) |
| I10 replace line-count criteria | Accepted (§14); test-coupling claim independently verified |

### Second-round corrections

| Item | Disposition |
|---|---|
| C1 queue overflow is fail-fast, not backpressure | Accepted (§6.2) — sync `feed()` uses `put_nowait()` → `TerminalFailed`; async handlers may `await` |
| C2 size/colour queries need their own callbacks | Accepted (§3.2, §3.4). **Measured**: this document's first draft was wrong — zero pixel dims do not make XTWINOPS reply; `OPT_SIZE` must be registered. Verified the remedy works |
| C3 intern-generation rollover invalidates shadow ids | Accepted (§4) — generation bump + forced full frame + atomic table/shadow swap, guarded by `Frame.generation` |
| C4 failure/reconnect output lifecycle | Accepted (§6.9, §6.10) — widget catches `GhosttyError` so it cannot reach `SshSession._pump` and recreate the ADR-0001 freeze; async `reset_io()` cancels and awaits the writer before terminal reset |

### Implementation-plan reconciliation

| Item | Disposition |
|---|---|
| Public cell ABI replaces private decoding | Verified — `GhosttyCell`/`GhosttyRow` are opaque `uint64_t`; rendering uses public accessors and `GhosttyCellWide` (§4) |
| Style rollover and frame lookup tables were one contract | Accepted — insertion raises before overflow; snapshot restarts forced; frames carry complete generation-scoped style/link tables (§3.1, §4) |
| Style limit must fit a full viewport | Accepted — effective limit is `max(configured, cols * rows)` on construction and resize; configured value bounds historical accumulation (§3.2, §4) |
| Link overflow cannot participate in rollover | Accepted — over-limit links return `None` and render as unlinked cells (§4, §6.11) |
| FFI output types and dirty lifecycle | Accepted — native calls use typed getters; cursor coordinates are gated by `HAS_VALUE`; global and row dirty flags are cleared independently (§3.1, §7) |
| Synchronous widget reset cannot cancel a send | Accepted — terminal-only `hard_reset()` is separate from awaited `reset_io()` (§6.10) |
| Resource limits omitted APC fields | Accepted — both APC limits are explicit policy and passed at construction (§3.2, §6.11) |

## Sources

- [libghostty-vt documentation](https://libghostty.tip.ghostty.org/)
- [Libghostty Is Coming — Mitchell Hashimoto](https://mitchellh.com/writing/libghostty-is-coming)
- [ghostty-org/ghostty `include/ghostty/vt.h`](https://github.com/ghostty-org/ghostty/blob/main/include/ghostty/vt.h)
- [ghostty-org/ghostling](https://github.com/ghostty-org/ghostling)
- [AnswerDotAI/pyghostty](https://github.com/AnswerDotAI/pyghostty) · [PyPI](https://pypi.org/project/pyghostty/)
- [coder/libghostty-vt-node](https://github.com/coder/libghostty-vt-node)
