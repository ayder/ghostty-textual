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

Install the package from PyPI:

```bash
pip install ghostty-textual
```

The distribution name is `ghostty-textual`; the Python import is `ghostty_textual`.

```python
from ghostty_textual import TerminalView

view = TerminalView(
    send=session.send,
    resize_transport=lambda cols, rows: session.resize(rows, cols),
)
session.on_output = view.feed
```

`feed()` parses bytes and queues replies immediately, then coalesces frame
extraction until the next event-loop turn. Call `view.refresh_frame()` after
feeding when you need to inspect the rendered frame synchronously. Synchronized
output still withholds frames until release or timeout; `refresh_frame(force=True)`
explicitly bypasses that hold.

The application remains responsible for process and PTY lifecycle. For
reconnects, close the old process, `await view.reset_io()`, call
`view.hard_reset()`, then start the replacement process.

## Design documents

- [`docs/superpowers/specs/2026-07-25-ghostty-textual-design-v2.md`](https://github.com/ayder/ghostty-textual/blob/main/docs/superpowers/specs/2026-07-25-ghostty-textual-design-v2.md)
  — current design
- [`docs/superpowers/specs/sol-5.6-textual-design-review.md`](https://github.com/ayder/ghostty-textual/blob/main/docs/superpowers/specs/sol-5.6-textual-design-review.md)
  — review that produced it
- [`docs/superpowers/specs/2026-07-25-ghostty-textual-design.md`](https://github.com/ayder/ghostty-textual/blob/main/docs/superpowers/specs/2026-07-25-ghostty-textual-design.md)
  — superseded v1

## Supported platforms

Requires Python 3.12+ and pins `pyghostty==0.1.4`, which bundles libghostty-vt
built for baseline CPUs. No native compilation is needed to install it.

| OS | Architectures | Minimum OS / libc |
|---|---|---|
| Linux | `amd64` / `x86_64`, `aarch64` / `arm64` | glibc 2.27 |
| macOS | `amd64` / `x86_64` (Intel), `arm64` / `aarch64` (Apple Silicon) | macOS 13 |

`amd64` and `x86_64` name the same architecture; so do `aarch64` and `arm64`.
Use a 64-bit OS and Python on ARM devices such as Raspberry Pi 5. Upstream
does not publish Windows, musl/Alpine Linux, or 32-bit ARM wheels.

CI runs the native ABI and functional tests, plus a clean wheel installation
and terminal smoke test, on Linux and macOS with both CPU architectures using
Python 3.12. An additional Linux x86_64 job runs the same checks with Python 3.15.
Python 3.15 requires CFFI 2.1.1 or newer to avoid an upstream callback ABI crash;
this minimum is enforced by the package dependencies.

The bundled native library retains a cell's base codepoint plus at most 64
additional grapheme codepoints. Further combining codepoints are ignored to
bound memory and processing costs. This applies across fragmented input too.
The scheduled ABI drift job tests the latest upstream binding separately from
the exact dependency used by normal CI and released distributions.

## Develop

```bash
uv sync --all-extras
uv run pytest
uv run ruff check src tests
```

The full-frame extraction benchmark defaults to a 10 ms p95 budget for a
120×40 terminal. GitHub CI enforces 100 ms on Linux and macOS ARM64. Hosted macOS
Intel records median/p95 and budget violations without failing on timing alone;
that runner has exceeded 100 ms even on the pre-optimization baseline. Functional,
memory, ABI, and packaging checks remain mandatory on every platform. Native
extraction errors still fail the timing step, including on Intel.

Set `GHOSTTY_TEXTUAL_FRAME_BUDGET_MS` to override the budget locally. The default
`GHOSTTY_TEXTUAL_FRAME_BUDGET_MODE=enforce` asserts the p95 budget; `report` records
timings without asserting the cutoff. CI runs the timing test separately with
output capture disabled so successful runs also retain measurements in their logs.

Additional benchmarks cover styled and linked extraction, row rendering,
selection, and fragmented input through a mounted widget:

```bash
uv run python benchmarks/frame_extraction.py
uv run python benchmarks/widget_rendering.py
uv run python benchmarks/feed_bursts.py
```

See [performance measurements and cache design](https://github.com/ayder/ghostty-textual/blob/main/docs/performance.md) for results
and measurement scope.

## Releasing

Update the version in `pyproject.toml` and `uv.lock`, merge the release PR, then
push a matching tag such as `v0.1.0`. The tag must point to the merged commit that
contains that version and the release workflow.

CI scans the full Git history with Gitleaks, then requires that scan and the
four-platform test matrix to pass before releasing. It also checks distribution
metadata and README rendering with `twine check --strict`.

CI builds and smoke-tests distributions on all four supported platforms. The
Linux x86_64 job retains its wheel and source archive as the `python-dist` Actions
artifact on every run. After all platform jobs pass on a `v*` tag push, the release
job verifies the tag, package version, filenames, and archive metadata, then
attaches the tested files to a GitHub Release. It creates a draft when needed and
publishes only after the uploads succeed; an existing release keeps its notes.
A separate job then publishes the same tested artifacts to PyPI as
`ghostty-textual` through Trusted Publishing, with provenance attestations.

The wheel is `ghostty_textual-<version>-py3-none-any.whl`: one wheel supports all
the listed platforms, with the native binary supplied by the pinned `pyghostty`
dependency. The source archive is `ghostty_textual-<version>.tar.gz`.

Retry a failed release job from Actions. Already uploaded assets are downloaded
and compared before being reused; differing files fail rather than replacing a
published asset. Only the GitHub release job receives `contents: write`; only the
PyPI job receives `id-token: write`. The PyPI job downloads the tested artifact
without checking out or building repository code. No PyPI API token is stored.

Before the first PyPI release:

1. Create the GitHub Actions environment `pypi` in `ayder/ghostty-textual`.
   Restrict its deployment tags to `v*`; add required reviewers if desired.
2. In the intended owner's [PyPI publishing settings](https://pypi.org/manage/account/publishing/),
   add a pending Trusted Publisher with project name `ghostty-textual`, owner
   `ayder`, repository `ghostty-textual`, workflow `ci.yml`, and environment `pypi`.
   For an existing project, add the publisher in that project's publishing settings.
3. Merge the release PR and push `v0.1.0`. PR and branch builds validate packages
   but do not upload to either registry. A failed PyPI job can be rerun after
   fixing publisher configuration. Already published versions cannot be replaced;
   use a new version for subsequent releases.

For a local secret scan, install [Gitleaks](https://github.com/gitleaks/gitleaks)
and run `gitleaks git --redact --no-banner --log-opts=--all .`.

## Licence

MIT
