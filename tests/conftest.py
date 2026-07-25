"""Shared harness for the native-layer tests.

These tests drive the raw libghostty-vt ABI through `_native.load()`. They exist
before `emulator.py` deliberately: they pin the behaviour the emulator will be
built on, so a libghostty or pyghostty change breaks here -- at the boundary --
rather than somewhere in a render loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ghostty_textual._native import Native, load


@pytest.fixture(scope="session")
def native() -> Native:
    return load()


@dataclass
class Harness:
    """One terminal plus whatever callbacks a test registered."""

    native: Native
    terminal: Any
    pty_writes: list[bytes] = field(default_factory=list)
    size_calls: list[int] = field(default_factory=list)
    # CFFI callback objects are kept alive here. If they are garbage collected
    # while C still holds the pointer, the next invocation is a hard crash --
    # this list is the mechanism spec v2 §3.2 requires of the real Terminal.
    _keepalive: list[Any] = field(default_factory=list)

    @property
    def ffi(self) -> Any:
        return self.native.ffi

    @property
    def lib(self) -> Any:
        return self.native.lib

    def feed(self, data: bytes) -> None:
        self.lib.ghostty_terminal_vt_write(self.terminal, data, len(data))

    def drain(self) -> list[bytes]:
        """Return and clear everything the terminal wrote back to the PTY."""
        written, self.pty_writes[:] = list(self.pty_writes), []
        return written

    def reply_to(self, data: bytes) -> list[bytes]:
        self.drain()
        self.feed(data)
        return self.drain()

    def get_u32(self, key: str) -> int | None:
        out = self.ffi.new("uint32_t*")
        rc = self.lib.ghostty_terminal_get(
            self.terminal, getattr(self.lib, f"GHOSTTY_TERMINAL_DATA_{key}"), out
        )
        return None if rc else out[0]

    def get_str(self, key: str) -> bytes | None:
        out = self.ffi.new("GhosttyString*")
        rc = self.lib.ghostty_terminal_get(
            self.terminal, getattr(self.lib, f"GHOSTTY_TERMINAL_DATA_{key}"), out
        )
        if rc or out.ptr == self.ffi.NULL:
            return None
        return bytes(self.ffi.buffer(out.ptr, out.len))

    def close(self) -> None:
        if self.terminal is not None:
            self.lib.ghostty_terminal_free(self.terminal)
            self.terminal = None


def make_terminal(
    native: Native,
    *,
    cols: int = 80,
    rows: int = 24,
    scrollback: int = 1000,
    write_pty: bool = True,
    size_report: bool = False,
) -> Harness:
    ffi, lib = native.ffi, native.lib
    handle = ffi.new("GhosttyTerminal*")
    options = ffi.new(
        "GhosttyTerminalOptions*", dict(cols=cols, rows=rows, max_scrollback=scrollback)
    )
    native.check(lib.ghostty_terminal_new(ffi.NULL, handle, options[0]), "terminal_new")

    harness = Harness(native=native, terminal=handle[0])

    if write_pty:

        @ffi.callback("void(GhosttyTerminal, void*, const uint8_t*, size_t)")
        def on_write_pty(term, userdata, data, length):  # noqa: ANN001, ARG001
            harness.pty_writes.append(bytes(ffi.buffer(data, length)))

        harness._keepalive.append(on_write_pty)
        native.check(
            lib.ghostty_terminal_set(
                harness.terminal, lib.GHOSTTY_TERMINAL_OPT_WRITE_PTY,
                ffi.cast("void*", on_write_pty),
            ),
            "set WRITE_PTY",
        )

    if size_report:

        @ffi.callback("bool(GhosttyTerminal, void*, GhosttySizeReportSize*)")
        def on_size(term, userdata, out_size):  # noqa: ANN001, ARG001
            harness.size_calls.append(1)
            out_size.rows, out_size.columns = rows, cols
            # A cell renderer cannot know physical pixels. Zero cell dimensions
            # are how libghostty reports "unknown" -- see spec v2 §3.4.
            out_size.cell_width, out_size.cell_height = 0, 0
            return True

        harness._keepalive.append(on_size)
        native.check(
            lib.ghostty_terminal_set(
                harness.terminal, lib.GHOSTTY_TERMINAL_OPT_SIZE, ffi.cast("void*", on_size)
            ),
            "set SIZE",
        )

    return harness


@pytest.fixture
def terminal(native: Native):
    harness = make_terminal(native)
    yield harness
    harness.close()


@pytest.fixture
def sized_terminal(native: Native):
    harness = make_terminal(native, size_report=True)
    yield harness
    harness.close()
