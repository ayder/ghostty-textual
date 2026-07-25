# ghostty-textual — design

- **Date:** 2026-07-25
- **Status:** Proposed
- **First consumer:** [`pysshmanager`](../../../../pysshmanager)
- **Supersedes for pysshmanager:** ADR-0001 (stay on pyte) once shipped

## 1. Problem

`pysshmanager` renders SSH sessions with `pyte` 0.8.2. Its own ADR-0001 documents
why that is untenable: 22 of pyte's 27 CSI handlers raise `TypeError` on a
private (`CSI ? … X`) sequence, and because pyte's parser is a coroutine driven
from `Stream.feed()`, the exception escapes into the caller and kills the read
loop. Vim 9 probing `modifyOtherKeys` with `CSI ? 4 m` froze a live pane while
the SSH process kept running.

pyte is effectively unmaintained. Compensating for it costs `pysshmanager` six
distinct workarounds and leaves known gaps: no DECCKM, no terminal query
responses, no OSC 8, no cursor shapes, no synchronized output, no grapheme
clustering.

ADR-0001 chose to stay on pyte because the only credible Python alternative,
`bittty`, had not implemented scrollback — and scrollback is a shipped
`pysshmanager` feature (5,000 lines of history, `Shift+PageUp/Down`, wheel
scroll, drag-select-to-copy).

## 2. What changed

`libghostty-vt` reframes the decision. It is not another young Python emulator —
it is Ghostty's shipping terminal core, extracted as a zero-dependency C library
that does not even require libc. Scrollback, line wrapping, and reflow on resize
are built in. The emulator is proven; only the Python binding is new, and a
binding is small enough to read, fix, or replace.

Three properties matter for this project specifically:

1. **Scrollback with reflow** — ADR-0001's blocker, resolved. pyte does not
   reflow at all.
2. **A dirty-cell render API** — `ghostty_render_state_begin()` /
   `ghostty_render_state_next_cell()`, the same path Ghostty's own Metal and
   OpenGL renderers use. It reports only changed cells.
3. **Input encoding is a function you call** — separate key, mouse, and focus
   encoders. Nothing is sent on your behalf. This is precisely what disqualified
   `textual-tty`, and it is structurally impossible here: ADR-0002 is preserved
   by simply never calling the mouse encoder.

### Verified facts (2026-07-25)

| | |
|---|---|
| `pyghostty` | 0.1.0, published 2026-07-24, repo created 2026-07-22, 3 stars |
| `pyghostty` license | Apache-2.0 / MIT |
| `pyghostty` runtime deps | `cffi` only |
| `pyghostty` wheels | `py3-none-` × {macosx_13_0_arm64, macosx_13_0_x86_64, manylinux_2_28_aarch64, manylinux_2_28_x86_64} |
| `pyghostty` binding mode | CFFI **ABI** mode over a bundled shared library |
| `libghostty-vt` API stability | **Explicitly unstable — breaking changes expected** |

`py3-none-<platform>` means one wheel per platform covers every Python version,
and the platform matrix exactly matches `pysshmanager`'s existing
"Linux + macOS only" limitation. `cffi` is *already* in `pysshmanager`'s tree via
`argon2-cffi → argon2-cffi-bindings → cffi`, so the net new runtime dependency
is `pyghostty` and its bundled shared library.

### Not yet verified — resolve on day one

**Does `pyghostty`'s generated `_cdef` actually include the `ghostty_render_state_*`
symbols?** Its README claims "the complete generated C API", but that is a claim,
not a measurement. Step one of implementation is a 15-minute spike: import
`pyghostty._ffi` and enumerate `lib` for those symbols. If absent, the fix is a
small upstream PR to cdef generation, not a redesign — see §10 for the fallback.

## 3. Decision

Build **`ghostty-textual`**: a reusable, published Textual terminal widget
library over `libghostty-vt`, with `pysshmanager` as its first consumer.

Bind to `libghostty-vt` through **`pyghostty`'s low-level `_ffi`/`lib`**, not its
high-level `core.Terminal`. `pyghostty` serves as the delivery vehicle for a
prebuilt, multi-platform shared library plus a generated cdef; we build our own
Pythonic layer on top. Coupling to `pyghostty`'s *Python* API stays near zero, so
an abandoned upstream costs one adapter module rather than a rewrite.

`core.Terminal` is rejected as a base: it exposes no render state (forcing
per-cell FFI on every refresh), no terminal-mode queries, and no key encoder.

## 4. Non-goals

- **The library never owns a process.** No spawning, no PTY lifecycle, no
  transport. Bytes in, bytes out. This is the constraint that disqualified
  `textual-tty` for `pysshmanager` and it is load-bearing here.
- **No Windows support** in v1 (matches `pysshmanager`; matches the wheel matrix).
- **No GPU/pixel rendering.** Cell grid to Rich `Segment`s only.
- **No dual-backend abstraction.** We do not keep a pyte path alive behind an
  interface; that would mean keeping the workarounds this project exists to
  delete.

## 5. Architecture

```
ghostty-textual/
  pyproject.toml          hatchling · deps: textual>=8.2, pyghostty>=0.1
  src/ghostty_textual/
    __init__.py           public exports
    emulator.py           Terminal — the only file that touches C
    cells.py              Cell, CellStyle, style interning
    keys.py               Textual events → libghostty key events
    widget.py             TerminalView(Widget)
    py.typed
  tests/
```

Two layers with one churn boundary:

- **`emulator.py`** — zero Textual imports, headless-testable. The single place a
  `libghostty-vt` API break can land.
- **`widget.py`** — zero C knowledge. Consumes only the `emulator.py` API.
- **`pty.py`** — deferred to v0.2. A batteries-included PTY-owning widget that a
  public library will eventually want. `pysshmanager` does not need it, and
  building it now would invite exactly the coupling §4 forbids.

Each unit is independently testable: `emulator.py` with byte fixtures and no
Textual, `widget.py` with a fake emulator and no C.

## 6. `emulator.py`

```python
class Terminal:
    def __init__(self, cols: int, rows: int, *, scrollback: int = 5000) -> None
    def feed(self, data: bytes) -> FeedResult
    def resize(self, cols: int, rows: int) -> None
    def reset(self) -> None
    def close(self) -> None                      # + context manager

    cols: int
    rows: int
    cursor: CursorState          # x, y, visible, shape, blink
    title: str | None
    modes: TerminalModes         # typed flags, not raw ints
    scrollback_rows: int

    def row(self, y: int, *, tag: RowTag = "viewport") -> Sequence[Cell]

    def encode_key(self, event: KeyEvent) -> bytes
    def encode_mouse(self, event: MouseEvent) -> bytes | None
    def encode_focus(self, focused: bool) -> bytes | None
    def encode_paste(self, text: str) -> bytes
```

### `feed()` takes bytes, not str — and this fixes a live bug

`pysshmanager`'s `session.py:_pump` currently does
`data.decode("utf-8", errors="replace")` on each 4096-byte read. A multi-byte
UTF-8 character straddling a read boundary is permanently corrupted into
replacement characters. `libghostty-vt` decodes incrementally across feeds, so
the bug disappears rather than being worked around.

### `feed()` returns a `FeedResult`

```python
@dataclass(frozen=True, slots=True)
class FeedResult:
    dirty_rows: frozenset[int]
    events: tuple[TerminalEvent, ...]   # TitleChanged | Bell | ClipboardWrite | ...
    frame_pending: bool
```

Returning the render contract rather than mutating in silence makes it
assertable in a headless test with no widget and no PTY, and it is what lets the
widget refresh *regions* instead of everything.

### Synchronized output lives here, not in the widget

While DECSET 2026 is active, `feed()` accumulates dirty rows and sets
`frame_pending=True`, withholding them until the frame closes. The widget honors
what it is given, so tear-free rendering costs the widget zero logic.

### `TerminalModes` is typed

```python
@dataclass(frozen=True, slots=True)
class TerminalModes:
    bracketed_paste: bool
    focus_events: bool
    app_cursor_keys: bool        # DECCKM
    alt_screen: bool
    mouse_tracking: MouseTracking   # NONE | X10 | NORMAL | BUTTON | ANY
    mouse_encoding: MouseEncoding   # X10 | SGR | URXVT
    sync_output: bool
```

This replaces `terminal_protocol.py`'s `private_mode_enabled()` and its
`_PRIVATE_MODE_SHIFT = 5` — a constant that exists only because pyte stores
unknown DEC private modes bit-shifted and publishes no accessor.

### Style interning

`libghostty-vt` reports attributes per cell; in practice a screen uses a handful
of distinct styles. `Cell` is `(text: str, style_id: int, link_id: int | None)`,
and Rich `Style` objects are built once per unique style and cached in `cells.py`.

`pysshmanager`'s current `_cell_style()` constructs a fresh `RichStyle` for
*every cell on every render*. Interning removes that entirely, and run-grouping
in `render_line` becomes an integer comparison instead of a `Style` comparison.

### Failure modes

- **Shared library unavailable** → raise `GhosttyUnavailable` at import with the
  running platform and the supported wheel matrix. Do not fail obscurely inside
  a widget mount.
- **Binding-level exception during `feed()`** → caught, logged at DEBUG, feed
  continues. This is a narrowed version of `pysshmanager`'s existing `_pump`
  guard. **Honest limitation:** a genuine segfault in C is not catchable and we
  do not pretend otherwise; the mitigation is the fuzz test in §9, not a
  `try`/`except`.
- **Use after `close()`** → `RuntimeError`. The C allocation is released by
  `close()`, the context manager, and a `__del__` safety net.

## 7. `widget.py` — `TerminalView`

```python
class TerminalView(Widget):
    def __init__(
        self,
        *,
        send: Callable[[bytes], Awaitable[None]],
        scrollback: int = 5000,
        mouse_mode: MouseMode = MouseMode.LOCAL,
        reserved_keys: frozenset[str] = frozenset(),
        terminal: Terminal | None = None,
    ) -> None

    terminal: Terminal
    def feed(self, data: bytes) -> None
    def reset(self) -> None

    class Resized(Message): cols: int; rows: int
    class TitleChanged(Message): title: str
    class Bell(Message): ...
    class LinkClicked(Message): uri: str
    class ClipboardWrite(Message): text: str
```

**Ownership.** The widget owns the `Terminal` and exposes it as `.terminal`; an
existing one may be injected for advanced use. The consumer drives it: its read
loop calls `view.feed(chunk)`, and the widget calls the injected `send` for
bytes destined for the child.

Keystrokes use the injected callable rather than a message, because the hot path
should not queue. Notifications (title, bell, link, clipboard) are Textual
messages, which is what they naturally are.

**Sizing.** The widget cannot know its size before mount, so the `Terminal` is
created at 80×24 and resized in `on_mount`/`on_resize` from `content_size` —
matching today's `sync_pty_size()`, including its deliberate use of
`content_size` rather than `size` so theme borders and padding do not desync the
PTY. Each resize posts `Resized(cols, rows)` so the consumer can resize its PTY.
This splits `pysshmanager`'s current `session.resize()`, which resizes emulator
and PTY together.

### Rendering

A Python-side **shadow buffer** of the viewport, updated from dirty cells:

1. `feed(data)` → `terminal.feed()` → apply changed cells into the shadow buffer,
   collect dirty rows.
2. `self.refresh(region)` for only those rows.
3. `render_line(y)` reads the shadow buffer — **zero FFI on the render path**.

The shadow buffer is what makes step 3 possible. Textual refreshes rows for its
own reasons (scroll, resize, theme change, selection change), and without a
Python-side copy every such refresh would re-enter C.

`render_line` builds `Segment`s by grouping runs of equal `style_id`, applies the
selection span, applies cursor inversion, and ends with `.apply_offsets(0, y)`.
**That last call is load-bearing** — ADR-0002 records that without it the
compositor cannot map a click to a character cell and drag-selection silently
yields nothing.

### Scrollback

The shadow buffer covers the viewport only. When scrolled up, rows are read via
`terminal.row(y, tag="history")`, where `y` counts back from the newest history
row (`0` = the line most recently scrolled off the viewport) and the widget
caches them in a dict on that index.

Note that this index is *not* stable: scrollback is a ring buffer, so every new
line that scrolls off shifts every history index by one. The cache is therefore
dropped whenever `scrollback_rows` changes, and on resize (which reflows) and
reset. That is cheap and correct — a user holding a scroll position while output
streams in is already seeing the view shift, and re-reading from C is acceptable
because the live screen is not what they are looking at.

An absolutely-stable key would require the emulator to expose a monotonic
count of total lines ever evicted. If profiling later shows the invalidation
hurting, that counter is the fix; it is not worth adding speculatively.

Scroll offset is clamped to `scrollback_rows` on every resize and reset.

### Selection

Ports `get_selection()` over `Cell` objects, keeping the trailing-whitespace trim
(the emulator pads rows to full width; without the trim a copied line carries the
padding). Adds wide-character correctness: continuation cells are skipped rather
than emitted as duplicates.

### OSC 8 hyperlinks

`Cell.link_id` indexes a link table on the emulator. Linked runs render
underlined. Click hit-testing is a shadow-buffer index at the click's cell
coordinates — no geometry work — and posts `LinkClicked(uri)`.

### Cursor shapes

`CursorState.shape` renders as: **block** → reverse (today's behavior),
**underline** → underline attribute, **bar** → falls back to reverse. A bar
occupies a sub-cell column that a character grid cannot represent, so this is a
documented limitation rather than a bug: `bar` and `block` look identical. Blink
is honored by a widget-level timer, disabled when the widget is unfocused.

### Mouse policy

```python
class MouseMode(Enum):
    LOCAL = auto()    # default
    REMOTE = auto()
```

- **`LOCAL`** — wheel scrolls scrollback, drag selects, **nothing is sent to the
  child**. `encode_mouse` is never called. ADR-0002 preserved exactly.
- **`REMOTE`** — when the child requests tracking, encode and send; fall back to
  `LOCAL` behavior when it is not tracking.

Default is `LOCAL`, so `pysshmanager` gets its policy by changing nothing, while
the library stays useful to people who want tmux click-to-select.

**This changes the shape of one existing guard test.** ADR-0002's consequences
name `tests/ssh/test_terminal_protocol.py`, which asserts that `encode_mouse`,
`MouseAction`, and `mouse_tracking_enabled` *do not exist*. That assertion cannot
survive — `encode_mouse` now exists by design. It is replaced by an equivalent
guard asserting `view.mouse_mode is MouseMode.LOCAL`. The invariant is unchanged;
only its enforcement point moves.

### Keys

`keys.py` translates a Textual `events.Key` into a libghostty `KeyEvent`
(keycode, mods, text, action); `terminal.encode_key()` produces the bytes,
honoring DECCKM and the Kitty keyboard protocol. This deletes `pysshmanager`'s
82-line hand-rolled mapping and closes both gaps ADR-0001 lists as knowingly
accepted.

`_APP_KEYS` — the set of keys reserved for app bindings and never forwarded —
becomes the `reserved_keys` constructor parameter, so the library does not
hardcode one application's keymap.

## 8. `pysshmanager` migration

Cut over in one step once `ghostty-textual`'s tests are green. No transitional
dual-backend: keeping pyte alive means keeping its workarounds.

| File | Change |
|---|---|
| `ssh/terminal_screen.py` | **deleted** (145 lines — all pyte compensation) |
| `ssh/terminal_protocol.py` | **deleted** (28 lines) |
| `ui/widgets/keys.py` | **deleted** (82 lines) |
| `ssh/session.py` | drop `_init_screen`, `screen`, `stream`, `terminal_modes`, and the `_pump` try/except; `_pump` passes bytes straight through; `resize()` resizes the PTY only |
| `ui/widgets/terminal.py` | 287 lines → ~60: wire `send`/`feed`/`Resized`, keep `ScrollLocal`, `ReleaseFocus`, and the `Ctrl+]` binding |
| `pyproject.toml` | drop `pyte`, add `ghostty-textual` |

Roughly 500 lines net: 255 in outright deletions, ~225 from shrinking
`terminal.py`, plus the emulator plumbing in `session.py`. All six workarounds in
ADR-0001's consequences table go with them.

`SshSession` ends up strictly better factored: process and transport only, with
no emulator state. That is the separation ADR-0001 was protecting when it said
"keep the seam clean."

**Write ADR-0006 in `pysshmanager`** superseding ADR-0001, recording that the
revisit conditions were met by a candidate ADR-0001 did not anticipate.

## 9. Testing

The existing `pysshmanager` suite is the migration harness — ADR-0001 already
established that its tests assert behavior rather than pyte internals.

**Tier 1 — emulator (headless, no Textual).** Feed byte fixtures; assert cells,
modes, dirty rows, cursor, and encoder output. Must include:

- **The regression that started this.** `CSI ? 4 m` must parse cleanly and leave
  the screen intact — under pyte this froze a pane permanently.
- **Vim capture replay.** ADR-0001 §"How to re-evaluate" specifies capturing real
  PTY output from vim, htop, and tmux; the original vim capture was 2,628 bytes.
  Replay each and assert the rendered screen.
- **UTF-8 split across feeds** — `feed(b"\xe2\x94")` then `feed(b"\x80")` yields
  `─`, not replacement characters.

**Tier 2 — widget (Textual `run_test()` pilot).** Rendering strips, selection
extraction, scrollback paging, key forwarding, resize propagation, link clicks,
mouse policy.

**Tier 3 — guards.** `emulator.py` imports no Textual (an import scan, in the
style of the existing `DEFAULT_CSS` hex scan). Default `mouse_mode` is `LOCAL`.

**Tier 4 — fuzz and benchmark.** Random and malformed byte streams asserting no
crash and no hang. Plus a benchmark gating full-screen redraw cost.

The benchmark exists because the perf argument for the dirty-cell design rests on
an *estimate* — CFFI ABI-mode calls are commonly cited at roughly microsecond
scale, which over a 200×50 grid (10,000 cells) would put a naive per-cell refresh
in the tens of milliseconds. That number must be **measured, not assumed**,
before it is used to justify anything. If a naive read turns out fast enough, the
shadow buffer is still correct but its urgency drops.

## 10. Risks

| Risk | Mitigation |
|---|---|
| Render-state symbols missing from `pyghostty`'s cdef | Day-one spike (§2). Fallback: upstream cdef PR, or grid-ref reads with row-level caching — slower, same architecture. |
| `libghostty-vt` API churn (**promised**, not hypothetical) | Contained in `emulator.py`. Pin `pyghostty` exactly; a scheduled CI job builds against latest to surface breaks early. |
| `pyghostty` abandoned (3 days old, 3 stars, 1 org) | Apache-2.0/MIT and vendorable. Escape hatch: build our own wheels with `ziglang` + `cibuildwheel` — which is exactly what `pyghostty` itself does, so the path is known-good. |
| Perf regression vs. pyte | Benchmark gate (§9). A rewrite that renders slower than pyte fails its own premise. |
| v1 scope is four new capabilities at once | Each is implemented in `libghostty-vt` already; cost lands in the widget. Sequence them so scrollback + selection + keys reach parity first, then OSC 8, cursor shapes, and sync output land incrementally against a green suite. |

## 11. Success criteria

1. vim, htop, and tmux are usable in a `pysshmanager` pane; `CSI ? 4 m` no longer
   freezes anything.
2. `pysshmanager`'s suite passes after migration, with ~500 lines and all six pyte
   workarounds deleted.
3. Full-screen redraw within the benchmark budget set in §9.
4. `ghostty-textual` is installable standalone with a documented BYO-transport
   contract and no `pysshmanager`-specific assumptions.

## 12. Deferred

- **`pty.py`** — batteries-included PTY-owning widget (v0.2).
- **Upstreaming to `pyghostty`** — once the render-state wrapper, encoders, and
  mode access prove out, offer them upstream. Doing it first would put velocity
  behind a three-day-old project's review cadence; doing it after costs nothing
  and is the better ecosystem outcome.
- **Kitty graphics protocol** — `libghostty-vt` supports it; no consumer needs it.

## Sources

- [libghostty-vt documentation](https://libghostty.tip.ghostty.org/)
- [Libghostty Is Coming — Mitchell Hashimoto](https://mitchellh.com/writing/libghostty-is-coming)
- [ghostty-org/ghostty `include/ghostty/vt.h`](https://github.com/ghostty-org/ghostty/blob/main/include/ghostty/vt.h)
- [ghostty-org/ghostling](https://github.com/ghostty-org/ghostling) — reference C consumer
- [AnswerDotAI/pyghostty](https://github.com/AnswerDotAI/pyghostty) · [PyPI](https://pypi.org/project/pyghostty/)
- [coder/libghostty-vt-node](https://github.com/coder/libghostty-vt-node) — independent binding, validates the pattern
