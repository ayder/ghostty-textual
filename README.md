# ghostty-textual

A [Textual](https://textual.textualize.io/) terminal widget backed by
[libghostty-vt](https://libghostty.tip.ghostty.org/), Ghostty's embeddable
terminal emulation core.

The headless emulator and `TerminalView` widget are implemented. The public
renderer uses libghostty's render-state and cell accessor APIs, keeps Ghostty's
viewport authoritative, and routes terminal replies and user input through one
ordered transport queue.

## Why

Existing Python terminal emulators force a choice: `pyte` is unmaintained and
structurally unprepared for modern escape sequences, while newer pure-Python
parsers are young enough that their own scrollback support is still in progress.
`libghostty-vt` is neither — it is Ghostty's shipping core, extracted as a
zero-dependency C library with scrollback, reflow, and grapheme handling built
in.

## Design principle

**The library never owns a process, PTY, or transport.** Bytes in, bytes out.

That constraint is what makes it usable by applications that already own their
process lifecycle — SSH clients with password injection and process-group
cleanup, for example — and it is why existing batteries-included Textual
terminal widgets were not an option.

Two layers, one native boundary:

| Module | Role |
|---|---|
| `_native.py` | Loader, ABI verification, union-by-value shims. The only file that knows `pyghostty` exists. |
| `_render.py` | Private public-ABI render-state extraction and dirty acknowledgement. |
| `emulator.py` | `Terminal` — headless, no Textual import. Absorbs libghostty API churn. |
| `widget.py` | `TerminalView` — rendering, input, selection. No C knowledge. |

## Minimal use

```python
from ghostty_textual import TerminalView

view = TerminalView(
    send=session.send,
    resize_transport=lambda cols, rows: session.resize(rows, cols),
)
session.on_output = view.feed
```

The application remains responsible for process and PTY lifecycle. For
reconnects, close the old process, `await view.reset_io()`, call
`view.hard_reset()`, then start the replacement process.

## Design documents

- [`docs/superpowers/specs/2026-07-25-ghostty-textual-design-v2.md`](docs/superpowers/specs/2026-07-25-ghostty-textual-design-v2.md)
  — current design
- [`docs/superpowers/specs/sol-5.6-textual-design-review.md`](docs/superpowers/specs/sol-5.6-textual-design-review.md)
  — review that produced it
- [`docs/superpowers/specs/2026-07-25-ghostty-textual-design.md`](docs/superpowers/specs/2026-07-25-ghostty-textual-design.md)
  — superseded v1

## Supported platforms

Requires Python 3.12+ and pins `pyghostty==0.1.1`, which bundles libghostty-vt
built for baseline CPUs. No native compilation is needed to install it.

| OS | Architectures | Minimum OS / libc |
|---|---|---|
| Linux | `amd64` / `x86_64`, `aarch64` / `arm64` | glibc 2.27 |
| macOS | `amd64` / `x86_64` (Intel), `arm64` / `aarch64` (Apple Silicon) | macOS 13 |

`amd64` and `x86_64` name the same architecture; so do `aarch64` and `arm64`.
Use a 64-bit OS and Python on ARM devices such as Raspberry Pi 5. Upstream
does not publish Windows, musl/Alpine Linux, or 32-bit ARM wheels.

CI runs the native ABI and functional tests, plus a clean wheel installation
and terminal smoke test, on Linux and macOS with both CPU architectures.

## Develop

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

The full-frame extraction benchmark defaults to a 10 ms p95 budget for a
120×40 terminal. GitHub CI uses 100 ms to accommodate hosted runner performance.
Set `GHOSTTY_TEXTUAL_FRAME_BUDGET_MS` to override the budget locally.

## Licence

MIT
