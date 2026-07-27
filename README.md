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

## Develop

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

Requires a platform with a `pyghostty` wheel: macOS arm64/x86_64, or glibc Linux
aarch64/x86_64.

## Licence

MIT
