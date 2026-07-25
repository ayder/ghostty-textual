# ghostty-textual design review

- **Reviewed:** 2026-07-25
- **Reviewer:** sol-5.6
- **Target:** `2026-07-25-ghostty-textual-design.md`
- **Recommendation:** Revise the contract before implementation

## Executive summary

The design makes the right high-level decision:

- `ghostty-textual` should not own a process or PTY.
- The headless emulator layer should be independent of Textual.
- `pysshmanager` is a good first-class consumer because it exercises transport
  ownership, resize, scrollback, selection, full-screen applications, and local
  mouse policy.
- `libghostty-vt` is a substantially better foundation than preserving pyte
  compatibility.

The proposed boundary is therefore sound. The current API contract is not quite
ready to implement, though. Six issues should be treated as blockers:

1. `feed()` currently conflates terminal mutation, effect delivery, render-state
   snapshotting, and synchronized-output policy.
2. Terminal-generated PTY writes are missing from `FeedResult`, so cursor/status
   queries would still go unanswered.
3. The history-index scrollback model would make a scrolled viewport move when
   new output arrives and bypasses Ghostty's own viewport model.
4. Ownership, close, reconnect/reset, and thread-affinity rules are incomplete.
5. Binding failures are specified to be swallowed, which could recreate the
   silent frozen/corrupt terminal behavior the project is meant to eliminate.
6. The spec relies on private `pyghostty._ffi` APIs with a loose dependency
   constraint despite explicitly acknowledging an unstable ABI.

Resolve those items and this becomes a strong implementable design.

## What is already strong

The following decisions should be retained:

- **BYO transport.** Bytes in and bytes out is the correct reusable boundary.
- **One native boundary.** Keeping all CFFI access in `emulator.py` contains API
  churn and gives the public library an independently testable core.
- **Raw bytes into the emulator.** This fixes UTF-8 sequences split across PTY
  reads and avoids unnecessary decoding.
- **A Python shadow viewport.** Textual can ask for a row for reasons unrelated
  to new PTY output; rendering must not depend on live C references.
- **Local mouse by default.** This preserves pysshmanager's selection and
  scrollback behavior.
- **Explicit reserved keys.** A reusable widget must not know pysshmanager's
  application shortcuts.
- **Behavioral fixtures and real full-screen captures.** Vim, htop, and tmux
  replay tests are the right compatibility evidence.
- **No transitional pyte backend.** Once parity is demonstrated, one backend is
  simpler and safer than maintaining two.

## Resolved facts and corrections

Several statements marked as uncertain can be resolved before implementation.

### Render-state symbols are present

`pyghostty` 0.1.0's generated `_cdef.py` contains:

- `ghostty_render_state_new`
- `ghostty_render_state_update`
- `ghostty_render_state_begin_update`
- `ghostty_render_state_end_update`
- global and row dirty-state accessors
- row and cell iterators
- multi-get APIs
- resolved foreground/background colors
- UTF-8 grapheme extraction
- cursor and selection render data

The §2 day-one uncertainty can therefore be removed.

The names currently used in §2 are not real public C functions:
`ghostty_render_state_begin()` and `ghostty_render_state_next_cell()` should be
replaced with the actual update → row iterator → row-cell iterator flow.

The render API does not directly "report only changed cells." It tracks global
and per-row dirty state, while callers iterate rows/cells and decide which dirty
rows to copy. The caller must clear both global and per-row dirty flags after
committing the frame; clearing only one does not clear the other.

### Encoders and effects are present

The generated ABI includes:

- key event and key encoder objects
- `ghostty_key_encoder_setopt_from_terminal`
- mouse event and mouse encoder objects
- `ghostty_focus_encode`
- `ghostty_paste_is_safe` and `ghostty_paste_encode`
- terminal callbacks for PTY writes, bell, title, working directory, size,
  color scheme, device attributes, clipboard writes, enquiry, and XT version

The design does not need to speculate about whether these exist. It does need to
define which are wrapped and the policy for each.

### Viewport scrolling is present, but its ABI path needs work

The ABI declares `ghostty_terminal_scroll_viewport()` with top, bottom, delta,
and absolute-row behaviors. However, its argument contains a union passed by
value. CFFI ABI mode cannot call such signatures directly.

`pyghostty._ffi` already implements a layout-identical struct-twin workaround
for `GhosttyPoint`, but its own comment says the same is needed "later" for
`GhosttyTerminalScrollViewport`; that second twin is not implemented in 0.1.0.

This is the concrete scrolling integration blocker. Add and test the twin on
macOS arm64/x86_64 and Linux arm64/x86_64, or use a tiny compiled C shim.
Upstreaming the fix to pyghostty is desirable, but `ghostty-textual` should not
block on an upstream release.

### Wheel tags

The Linux wheels are dual-tagged:

- `manylinux_2_27_aarch64.manylinux_2_28_aarch64`
- `manylinux_2_27_x86_64.manylinux_2_28_x86_64`

The platform table should preserve the complete tags rather than shortening
them to `manylinux_2_28`.

### Measured render cost

A local macOS arm64 measurement against the released 0.1.0 wheel produced:

- high-level per-cell `ref()` + `style()` reads, 120×40: about **38.6 ms**
- raw `GhosttyRenderState` row/cell iteration, including grapheme and style
  access, 120×40: about **4.1 ms**
- plain visible-screen formatting without styles: about **0.26 ms**

This validates the render-state/shadow-buffer direction. Record the benchmark
script in the repository and define a p95 budget rather than leaving success
criterion 3 unquantified. A reasonable initial target is a 120×40 full-frame
extraction under 10 ms on the supported macOS CI runner, with a separate
single-dirty-row benchmark.

## Blockers

### B1. Separate effects from rendering

`feed()` is currently expected to:

- mutate terminal state,
- gather callbacks/events,
- update render state,
- return dirty rows,
- and implement synchronized-output withholding.

This does not cover mutations that happen without a feed:

- resize and reflow,
- reset,
- viewport scroll,
- theme/default-color change,
- terminal selection change,
- cursor visibility changes caused by viewport movement.

It also makes `feed()` consume Ghostty's render dirty state even if the consumer
is not ready to commit a frame.

Use two explicit operations:

```python
@dataclass(frozen=True, slots=True)
class TerminalEffects:
    pty_writes: tuple[bytes, ...]
    notifications: tuple[TerminalNotification, ...]


@dataclass(frozen=True, slots=True)
class Frame:
    generation: int
    cols: int
    rows: int
    full_redraw: bool
    row_patches: tuple[RowPatch, ...]
    cursor: CursorState
    viewport: ViewportState
    frame_pending: bool


class Terminal:
    def feed(self, data: bytes) -> TerminalEffects: ...
    def snapshot(self, *, force: bool = False) -> Frame | None: ...
```

`resize()`, `reset()`, `scroll_viewport()`, and theme changes mutate the
terminal; the next `snapshot()` describes the resulting frame. `snapshot()`
owns the render-state update and dirty-flag lifecycle. Returned frames and cells
must be immutable Python copies; no public object may borrow a C grid reference
past the next terminal mutation.

The widget's update sequence becomes deterministic:

1. `effects = terminal.feed(data)`
2. enqueue `effects.pty_writes` in order and post notification messages
3. `frame = terminal.snapshot()`
4. atomically apply the frame to the shadow buffer
5. only then clear/acknowledge dirty state and refresh changed Textual rows

If clearing dirty state must happen inside `snapshot()`, document that the frame
is a complete owned copy and that a Python exception while applying it requires
`snapshot(force=True)` on recovery.

Do not reimplement DECSET 2026 from `TerminalModes` unless measurements of the
actual library show it is necessary. First establish exactly how Ghostty's
render-state dirty flags behave during synchronized output, including an
unterminated synchronized frame. A missing closing sequence must not freeze the
widget indefinitely.

### B2. Surface terminal-generated PTY writes

The proposed events mention title, bell, and clipboard, but omit the most
important effect: `GHOSTTY_TERMINAL_OPT_WRITE_PTY`.

This callback emits responses such as the cursor-position reply. A local probe
with `hello + CSI 6 n` produced `CSI 1;6 R`, confirming the callback works
through CFFI. If these bytes are not returned to the transport, one of the
documented pyte gaps remains unfixed.

Add ordered `pty_writes` to `TerminalEffects`. The C callback is synchronous
during `ghostty_terminal_vt_write`; it must only copy bytes into an instance
queue. It must not call `vt_write`, await Python code, or perform blocking work.
Drain the queue after `vt_write` returns.

Also define a terminal capability/effect policy:

```python
@dataclass(frozen=True, slots=True)
class TerminalProfile:
    term: str
    xtversion: str
    color_scheme: ColorScheme
    device_attributes: DeviceAttributes
    allow_clipboard_write: bool = False
    max_clipboard_bytes: int = 1_000_000
```

Some Ghostty effects return values synchronously rather than merely notifying:
device attributes, size, color scheme, enquiry, and XT version. State which
callbacks are configured, what values they return, and which are intentionally
unsupported. This terminal identity should be stable and tested.

Keep all CFFI callback objects strongly referenced on `Terminal` until after the
C terminal is freed. No exception may escape a CFFI callback; capture it and
raise from the outer Python operation.

### B3. Use Ghostty's viewport for scrollback

The proposed `"history"` row cache has two undesirable semantics:

- every new output line invalidates and shifts the entire cache;
- a user reading old output sees their content move when new output arrives.

The second point is not normal terminal behavior and should not be accepted as
"already seeing the view shift." A scrolled terminal should remain anchored to
the same content while output continues, until history eviction makes that
impossible.

Use `ghostty_terminal_scroll_viewport()` and render the resulting viewport
through `GhosttyRenderState`. Expose:

```python
class Terminal:
    def scroll_viewport(self, request: ScrollRequest) -> None: ...
    def scroll_to_top(self) -> None: ...
    def scroll_to_bottom(self) -> None: ...
    @property
    def viewport(self) -> ViewportState: ...


@dataclass(frozen=True, slots=True)
class ViewportState:
    at_bottom: bool
    offset: int
    scrollback_rows: int
    total_rows: int
```

The widget no longer needs a separate history-row cache: its shadow buffer is
always the current viewport. Page-up, wheel, and page-down mutate Ghostty's
viewport and request a new frame.

Required tests:

- output arriving while scrolled up keeps the same visible content anchored;
- scrolling to bottom follows new output again;
- history eviction clamps safely;
- primary resize reflows history and preserves a sensible anchor;
- alternate-screen entry and exit have explicit viewport behavior;
- `max_scrollback=5000` is empirically bounded as expected.

### B4. Define ownership, lifecycle, reset, and thread affinity

The widget says it owns the terminal while also accepting an injected terminal.
Specify conventional ownership:

- terminal constructed by `TerminalView` → closed by `TerminalView`;
- injected terminal → borrowed by default and never closed by the widget;
- optionally allow `close_terminal=True` to transfer ownership explicitly.

Define unmount behavior, idempotent close, pending-send cancellation/draining,
and whether a remounted widget may reuse a borrowed terminal.

`ghostty_terminal_reset()` semantics must be tested before using it as the
reconnect reset. In particular, determine whether it clears scrollback, title,
palette/OSC overrides, selection, modes, alternate-screen state, and pending
effects. For reconnect, pysshmanager needs a completely clean terminal. If the
C reset preserves anything, implement `hard_reset()` by freeing and recreating
the terminal, render state, iterators, encoders, and callbacks with retained
configuration.

Moving the emulator out of `SshSession` changes reconnect orchestration. The
current `SshSession.reconnect()` resets its own screen internally. After this
design, the transport cannot reset the widget it no longer owns. The migration
section must state who calls `view.hard_reset()` and in what order relative to
restarting the process.

`libghostty-vt` is not thread-safe. Make `Terminal` explicitly thread-confined:
creation, feed, snapshot, resize, scroll, encoding, and close must occur on the
same thread/event loop. If bytes arrive from another thread, the caller must
marshal them onto the owner loop. Add a DEBUG-only owner-thread assertion if it
is cheap.

Do not rely on `__del__` for correctness. Use explicit close/context management;
`weakref.finalize` may be a last-resort leak guard if callback cycles are
carefully avoided.

### B5. Do not swallow binding errors

The failure-mode proposal says a binding-level exception during `feed()` is
caught, logged at DEBUG, and feeding continues. That is unsafe.

`ghostty_terminal_vt_write()` is deliberately non-failing for untrusted terminal
input. Malformed VT bytes are handled by the library. Therefore a Python/CFFI
exception indicates a wrapper bug, ABI mismatch, invalid lifecycle, allocation
failure elsewhere, or corrupted assumptions. Continuing with partially updated
state can silently freeze or misrender the pane.

Recommended policy:

- malformed remote bytes: handled by libghostty, never a Python exception;
- `GHOSTTY_TERMINAL_DATA_VT_PROCESSING_ERROR`: expose as diagnostics and test
  its meaning, but do not crash merely because untrusted input was rejected;
- non-success `GhosttyResult` or Python wrapper error: raise a typed fatal
  `GhosttyError`;
- widget converts a fatal emulator error into an explicit terminal-failed state
  and notification; it must not silently display stale state as connected.

Fuzz tests that may discover native crashes must run the parser in subprocesses
with timeouts. Running arbitrary bytes in the pytest process cannot report a
segfault cleanly.

### B6. Pin and verify the private ABI dependency

The architecture declares `pyghostty>=0.1`, while §10 says to pin exactly.
Those positions conflict. Use:

```toml
dependencies = [
  "textual>=8.2.8,<9",
  "pyghostty==0.1.0",
]
```

Expand supported ranges intentionally after CI passes. `pyghostty._ffi` and
`pyghostty._cdef` are private modules, so even a nominally compatible pyghostty
release may change them without deprecation.

Add a startup compatibility check for required symbols and expected sized
structs. Record the bundled Ghostty commit (`88b4cd0` in pyghostty 0.1.0) in the
adapter and diagnostics. The `PYGHOSTTY_LIB` environment override can load an
arbitrary ABI-incompatible shared library; either reject/document it as
unsupported or validate as much build information and type layout as possible.

`GhosttyUnavailable` should normally be raised when constructing `Terminal`,
not while importing `ghostty_textual`. Lazy loading lets documentation, type
checking, package metadata, and applications with optional terminal features
import on unsupported platforms. The construction error can still include the
platform and wheel matrix.

## Important improvements

### I1. Specify the complete cell and style model

`Cell(text, style_id, link_id)` is insufficient to distinguish an ordinary
empty cell from the continuation of a wide grapheme.

At minimum:

```python
@dataclass(frozen=True, slots=True)
class Cell:
    text: str
    width: Literal[0, 1, 2]  # 0 = continuation
    style_id: int
    link_id: int | None
```

Specify how spacer-head/tail cells, combining graphemes, tabs, invalid UTF-8,
and over-wide graphemes map into the fixed Textual grid.

`CellStyle` should cover:

- default, palette, and RGB foreground/background;
- bold, faint, italic, blink, inverse, invisible, strike, and overline;
- underline kind and underline color;
- hyperlink presentation policy.

Ghostty render state can provide resolved cell colors. Define whether style IDs
contain resolved colors or symbolic palette references. If symbolic, an OSC
palette/default-color change must invalidate affected Rich styles and rows. If
resolved, the resolved RGB values naturally participate in the intern key.

Style and hyperlink interning must be bounded or generation-scoped. A hostile
remote can emit indefinitely many true-color combinations or OSC 8 URIs; a
process-lifetime dictionary would be an unbounded memory sink.

### I2. Add a terminal theme/default-color API

The design does not say how Textual's theme becomes Ghostty's default
foreground, background, cursor, and 256-color palette. Without this, default
cells, reverse video, OSC color overrides, and selection/cursor rendering can
disagree with the widget background.

Add a `TerminalTheme`/`ColorPalette` configuration and a method to update it.
Theme changes should force a full snapshot and rebuild Rich style mappings.
Define the precedence:

1. embedder defaults;
2. terminal OSC overrides;
3. selection overlay;
4. cursor overlay.

### I3. Use one ordered output path

`send: Callable[[bytes], Awaitable[None]]` is reasonable, but the statement that
the keystroke hot path "should not queue" conflicts with synchronous C callbacks
that generate PTY output during `feed()`.

Keys, paste, focus, optional mouse, and terminal query replies must share one
FIFO so their relative order is deterministic. Implement a small writer task or
require the consumer to provide an ordered `send_nowait()` queue. Define:

- ordering;
- bounded backpressure;
- behavior when `send` raises;
- close/drain/cancel behavior;
- whether input before mount is rejected or buffered.

Queueing a few bytes is not a meaningful latency cost compared with an SSH/PTY
write; correctness is more important here.

An alternative is `async def TerminalView.feed()`, allowing the pysshmanager
pump to await PTY replies directly. If chosen, update `SshSession.on_output` to
an async bytes callback and specify error propagation. Do not leave a sync
`feed()` that launches unordered background send tasks.

### I4. Avoid a first-resize race

Posting `Resized` is asynchronous. pysshmanager currently guarantees the real
layout size is applied to `SshSession` before the SSH process starts. Merely
posting a message from `on_mount` may let `SessionTabs.add_session()` return and
spawn SSH before the message handler updates `_rows` and `_cols`.

Either:

- inject a synchronous `resize_transport(cols, rows)` callback and also post the
  message for observers; or
- require the first consumer to explicitly call/await a public
  `sync_terminal_size()` before starting its process.

Coalesce duplicate resize events, reject zero dimensions, and document argument
order consistently. The current code uses `session.resize(rows, cols)`, while
the proposed API and `Resized` use `(cols, rows)`, which is an easy integration
bug.

Ghostty's C resize also accepts pixel cell dimensions. A Textual cell renderer
usually cannot know physical pixels. Define the values used and how XTWINOPS
pixel-size queries are answered rather than silently inventing 8×16.

### I5. Tighten selection semantics

Clarify whether v1 preserves pysshmanager's current display-row extraction or
adopts terminal-native copy semantics:

- trim trailing padding;
- join hard line breaks with `\n`;
- unwrap soft-wrapped rows or preserve display wraps;
- skip wide continuation cells;
- define selection across empty rows;
- define behavior while output, reflow, or scrolling changes the viewport.

Textual's compositor selection and Ghostty's native selection APIs should not
both be authoritative. A simple v1 can keep Textual endpoints and extract from
the immutable viewport shadow. A later version can translate endpoints to
Ghostty grid references and use Ghostty's selection formatter for soft-wrap
semantics.

Keep `.apply_offsets(0, y)` and its regression test.

### I6. Make key/paste behavior explicit

Before every key encode, call
`ghostty_key_encoder_setopt_from_terminal()` so DECCKM, keypad mode,
modifyOtherKeys, Kitty flags, and backarrow mode reflect current terminal state.

Change the headless signature to `bytes | None`; some Textual events cannot or
should not be represented. Specify mappings for:

- Textual canonical key names;
- printable `event.character`;
- ctrl/alt/shift/meta modifiers;
- consumed modifiers and unshifted codepoint;
- function/navigation/keypad keys;
- press/repeat/release limitations (Textual normally provides press events);
- unknown keys.

The widget must check `reserved_keys` before encoding and must leave the event
unstopped so priority application bindings can handle it.

Paste policy needs equal precision. Use `ghostty_paste_is_safe()` and define what
happens for unsafe unbracketed paste containing control characters. Test empty,
multiline, non-ASCII, and payloads containing bracketed-paste delimiter bytes.

### I7. Reduce v1 mouse scope

Remote mouse support adds capture, motion coalescing, coordinate translation,
wheel encoding, protocol modes, local-selection escape gestures, and conflicts
with OSC 8 click handling. It is not needed by the first consumer.

Prefer:

```python
class MouseMode(Enum):
    LOCAL = auto()
```

for v0.1 and defer `REMOTE` to a later release. The headless mouse encoder can
still be implemented/tested without exposing remote capture in the widget.

If `REMOTE` remains in v1, define a modifier that temporarily forces local
selection/scrolling, capture/release behavior, and how links are activated.
Do not describe remote tmux interaction as "click-to-select"; the click belongs
to the remote application, not Textual selection.

### I8. Define hyperlink and clipboard security

OSC content is untrusted remote input.

- `LinkClicked` should only report a URI; the library must never open it.
- Prefer an explicit modifier or consumer confirmation before opening.
- Bound URI length and the number of interned links.
- Reject/control dangerous or malformed schemes at the application policy
  layer, not silently inside the renderer.
- Clipboard writes should be disabled by default.
- Bound clipboard payload size and state whether non-text/binary data is
  rejected.
- Rate-limit or coalesce bell/title/clipboard notifications to avoid UI-message
  floods.

Kitty image storage should explicitly be disabled or assigned a small limit in
v1 even though rendering is deferred. Parsing supported graphics without a
storage policy creates unnecessary memory exposure.

### I9. Refine cursor rendering

Use the render state's viewport cursor fields, not active-screen coordinates.
This correctly hides the cursor while scrolled away from the live bottom and
handles wide-tail state.

Document:

- focus and scrollback visibility rules;
- hollow-block mapping;
- blink timer reset behavior;
- which row is invalidated when blink toggles;
- how cursor overlay composes with inverse video and selection.

Falling back from bar to reverse block is acceptable for a cell renderer.

### I10. Replace line-count success criteria

"~500 lines deleted" is useful motivation but should not be a success
criterion. It can pressure the implementation toward missing error handling or
public contracts.

Use:

- no runtime `pyte` dependency or imports;
- all pyte compensation modules removed;
- parity behaviors demonstrated;
- explicit performance and memory budgets;
- clean install/import tests on every supported wheel platform.

The existing pysshmanager suite is not entirely backend-neutral despite ADR-0001:

- `tests/ssh/test_session.py` directly uses `session.stream` and
  `session.screen.display`;
- `tests/ui/test_terminal.py` directly feeds `session.stream`, reads
  `_get_lines()`, and assumes pyte cell behavior;
- terminal-protocol tests encode pyte's shifted private modes.

Treat these as tests to rewrite around public behavior, not tests expected to
survive unchanged.

## Suggested public contract

This is one possible consolidated shape:

```python
class Terminal:
    def __init__(
        self,
        cols: int,
        rows: int,
        *,
        scrollback: int = 5000,
        profile: TerminalProfile | None = None,
        theme: TerminalTheme | None = None,
    ) -> None: ...

    def feed(self, data: bytes) -> TerminalEffects: ...
    def snapshot(self, *, force: bool = False) -> Frame | None: ...
    def resize(
        self,
        cols: int,
        rows: int,
        *,
        cell_width_px: int = 0,
        cell_height_px: int = 0,
    ) -> None: ...
    def scroll_viewport(self, request: ScrollRequest) -> None: ...
    def set_theme(self, theme: TerminalTheme) -> None: ...
    def hard_reset(self) -> None: ...
    def close(self) -> None: ...

    def encode_key(self, event: KeyEvent) -> bytes | None: ...
    def encode_focus(self, focused: bool) -> bytes | None: ...
    def encode_paste(self, text: str) -> bytes: ...

    @property
    def modes(self) -> TerminalModes: ...
    @property
    def title(self) -> str | None: ...
    @property
    def viewport(self) -> ViewportState: ...
```

`TerminalView` then owns:

- the Python viewport shadow;
- conversion from `Frame` cells/styles to Rich strips;
- Textual selection;
- cursor blink timing;
- local scroll gestures;
- Textual notification messages;
- ordered delivery of encoded input and `TerminalEffects.pty_writes`;
- size propagation according to the documented consumer contract.

The headless `Terminal` owns:

- all native allocations and callbacks;
- effect collection;
- render-state snapshots and dirty acknowledgements;
- viewport state;
- terminal modes and encoders;
- terminal identity/theme options;
- strict lifecycle and thread-affinity checks.

## pysshmanager integration changes to add to the spec

The migration section should explicitly describe these contract changes:

1. Change `SshSession.on_output` from `Callable[[], None]` to a callback receiving
   raw bytes. If widget feeding is async, make the callback awaitable and await
   it from `_pump`.
2. Attach the output callback before starting SSH. The current UI already mounts
   the terminal before process start; preserve that ordering.
3. Preserve the initial-size guarantee. A posted `Resized` message alone is not
   enough unless `SessionTabs.add_session()` awaits its handling.
4. Route query replies from `TerminalEffects.pty_writes` through the same ordered
   `SshSession.send()` path as keys and paste.
5. Define reconnect orchestration and invoke `view.hard_reset()` before the new
   process emits output.
6. Detach the output callback before unmount/close and define what happens to a
   final PTY chunk racing with pane removal.
7. Keep app-reserved key bindings and `Ctrl+]` release behavior in the thin
   pysshmanager wrapper.
8. Keep remote mouse disabled regardless of modes requested by the child.
9. Add a transport-only `SshSession` test and move emulator assertions to
   ghostty-textual's test suite.

## Test matrix additions

In addition to the proposed tests, require:

### Native lifecycle

- double close;
- every public operation after close;
- close with callbacks/events pending;
- hard reset clears history, modes, title, palette overrides, selection, and
  alternate-screen state;
- callback objects survive GC until close;
- repeated create/feed/close loop under a leak checker.

### Effects and identity

- DSR cursor-position response is written to PTY;
- DA1/DA2/DA3, XT version, size, color-scheme, and enquiry policy;
- event ordering when one feed produces title, bell, and PTY writes;
- OSC 52 disabled/default and payload limits;
- callback exception becomes a fatal Python error after `vt_write` returns.

### Rendering

- full, partial, and clean dirty states;
- dirty flags are acknowledged correctly;
- cursor moving between rows invalidates old and new rows;
- theme and OSC palette changes refresh affected content;
- default/reverse/true-color/256-color combinations;
- every underline type and color;
- combining graphemes, emoji ZWJ sequences, ambiguous-width text, and wide
  characters at the right margin;
- synchronized output, including missing terminator behavior.

### Scrollback and selection

- viewport remains anchored while new output arrives;
- bottom-follow resumes;
- ring eviction;
- resize/reflow while scrolled;
- primary/alternate transitions;
- hard vs soft line-break copy semantics;
- wide-tail endpoints and selection spanning empty rows.

### Input

- DECCKM normal/application cursor sequences;
- keypad mode;
- modifyOtherKeys;
- Kitty keyboard flags;
- ctrl/alt/shift combinations;
- bracketed/unbracketed and unsafe paste;
- focus events enabled and disabled;
- reserved keys never encoded or stopped.

### Packaging

- build wheel, install into a clean environment, import, construct, feed, and
  close;
- macOS arm64/x86_64 and glibc Linux arm64/x86_64 smoke jobs;
- unsupported platform error;
- missing/corrupt shared library error;
- required-symbol and ABI-layout check.

## Recommended implementation order

1. **Native loader and lifecycle:** exact dependency pin, compatibility checks,
   terminal/render-state allocation, idempotent close, callback retention.
2. **Effects:** PTY write callback and a minimal terminal identity profile.
3. **Frame extraction:** immutable cells/styles/cursor through render state,
   dirty acknowledgement, benchmark.
4. **Viewport:** scrolling ABI shim, anchoring tests, resize/reflow.
5. **Minimal widget:** shadow viewport, row rendering, sizing, ordered send path.
6. **Parity:** local scrolling, selection, keys, paste, focus, cursor.
7. **Security/features:** title, bell, OSC 8, clipboard policy, resource limits.
8. **pysshmanager cutover:** transport callback, initial sizing, reconnect,
   wrapper bindings, deletion of pyte code.
9. **Optional remote mouse:** only after the first-class consumer is stable.

## Go/no-go checklist for pysshmanager cutover

- [ ] Render-state API is used; no high-level per-cell `Terminal.style()` loop.
- [ ] PTY query responses are delivered in order.
- [ ] Scrolled content remains anchored while output arrives.
- [ ] Hard reset/reconnect produces a clean terminal.
- [ ] All native objects and callbacks have deterministic ownership.
- [ ] Binding/ABI failures are fatal and visible, not DEBUG-only drops.
- [ ] Exact pyghostty version and required ABI are checked.
- [ ] Initial PTY size is set before SSH starts.
- [ ] Local mouse, selection, and scrollback policy is preserved.
- [ ] Full-screen, partial-row, and memory benchmark budgets pass.
- [ ] Vim, htop, and tmux captures and interactive smoke tests pass.
- [ ] pysshmanager contains no pyte import or compensation path.

With these revisions, the proposed library can be both a clean public Textual
component and a genuinely first-class terminal engine for pysshmanager rather
than only a parser replacement.
