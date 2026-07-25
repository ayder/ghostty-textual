"""The native boundary: loader, ABI verification, and union-by-value shims.

This is the only module that knows `pyghostty` exists. Everything above it sees
`Native` and the exceptions defined here.

`pyghostty._ffi` and `pyghostty._cdef` are **private** modules of a pinned
dependency (see `pyproject.toml`). A nominally compatible release may change
them without deprecation, so `load()` verifies every symbol and struct layout it
depends on before returning.
"""

from __future__ import annotations

import functools
import platform
from dataclasses import dataclass
from typing import Any

__all__ = ["GhosttyError", "GhosttyUnavailable", "Native", "load"]


class GhosttyUnavailable(RuntimeError):
    """libghostty-vt could not be loaded, or its ABI is not the one we pinned."""


class GhosttyError(RuntimeError):
    """A libghostty-vt call failed, or the binding itself misbehaved.

    Always fatal. `ghostty_terminal_vt_write` is deliberately non-failing for
    untrusted input, so this never means "the remote sent bad bytes" -- it means
    a wrapper bug, ABI mismatch, or lifecycle violation. See spec v2 §3.6.
    """


SUPPORTED_WHEELS = (
    "macosx_13_0_arm64",
    "macosx_13_0_x86_64",
    "manylinux_2_27_aarch64.manylinux_2_28_aarch64",
    "manylinux_2_27_x86_64.manylinux_2_28_x86_64",
)

#: Symbols we call. Verified at load so a pyghostty change fails loudly here
#: rather than as an AttributeError deep inside a render loop.
REQUIRED_SYMBOLS = (
    # lifecycle
    "ghostty_terminal_new",
    "ghostty_terminal_free",
    "ghostty_terminal_reset",
    "ghostty_terminal_resize",
    "ghostty_terminal_set",
    "ghostty_terminal_get",
    "ghostty_terminal_vt_write",
    "ghostty_terminal_scroll_viewport",
    # render state
    "ghostty_render_state_new",
    "ghostty_render_state_free",
    "ghostty_render_state_update",
    "ghostty_render_state_begin_update",
    "ghostty_render_state_end_update",
    "ghostty_render_state_row_iterator_new",
    "ghostty_render_state_row_iterator_next",
    "ghostty_render_state_row_iterator_free",
    "ghostty_render_state_row_cells_new",
    "ghostty_render_state_row_cells_next",
    "ghostty_render_state_row_cells_free",
    # encoders
    "ghostty_key_encoder_new",
    "ghostty_key_encoder_free",
    "ghostty_key_encoder_encode",
    "ghostty_key_encoder_setopt_from_terminal",
    "ghostty_focus_encode",
    "ghostty_paste_is_safe",
    "ghostty_paste_encode",
)

# ABI-mode cffi cannot pass unions by value. pyghostty ships a layout-identical
# struct twin for GhosttyPoint and notes that GhosttyTerminalScrollViewport
# needs the same treatment "later"; as of pyghostty 0.1.0 that twin does not
# exist, so we carry it.
#
# The union is {intptr_t delta | size_t row | uint64_t _padding[2]} = 16 bytes,
# making the struct tag(4) + pad(4) + 16 = 24 bytes. Over the 16-byte threshold,
# so arm64 AAPCS64 passes it indirectly and x86-64 SysV passes it in stack
# memory -- in both cases identically to a struct of the same layout.
_SCROLL_TWIN_CDEF = """
typedef struct { uint64_t a; uint64_t b; } GhosttyTerminalScrollViewportValueS;
typedef struct {
    GhosttyTerminalScrollViewportTag tag;
    GhosttyTerminalScrollViewportValueS value;
} GhosttyTerminalScrollViewportS;
"""

_SCROLL_VIEWPORT_SIG = "void(*)(GhosttyTerminal, GhosttyTerminalScrollViewportS)"

#: (twin, real) pairs whose size and alignment must match exactly.
_TWIN_PAIRS = (
    ("GhosttyPointS", "GhosttyPoint"),
    ("GhosttyTerminalScrollViewportS", "GhosttyTerminalScrollViewport"),
)


@dataclass(frozen=True, slots=True)
class Native:
    """A verified handle on libghostty-vt."""

    ffi: Any
    lib: Any

    def check(self, result: int, what: str) -> None:
        """Raise `GhosttyError` unless `result` is GHOSTTY_SUCCESS (0)."""
        if result:
            raise GhosttyError(f"{what} failed: {result}")

    def scroll_viewport(self, terminal: Any, tag: int, value: int = 0) -> None:
        """Call `ghostty_terminal_scroll_viewport` through the struct twin."""
        fn = self._union_fn("ghostty_terminal_scroll_viewport", _SCROLL_VIEWPORT_SIG)
        behavior = self.ffi.new("GhosttyTerminalScrollViewportS*")
        behavior.tag = tag
        behavior.value.a = value & 0xFFFFFFFFFFFFFFFF
        behavior.value.b = 0
        fn(terminal, behavior[0])

    def _union_fn(self, name: str, sig: str) -> Any:
        from pyghostty._ffi import union_fn

        return union_fn(name, sig)


@functools.cache
def load() -> Native:
    """Load and verify libghostty-vt.

    Cached, so the cdef extension and layout assertions run exactly once.

    Raises:
        GhosttyUnavailable: the library is missing, or its ABI is not ours.
    """
    try:
        from pyghostty._ffi import ffi, lib
    except (ImportError, OSError) as exc:
        raise GhosttyUnavailable(
            f"libghostty-vt is unavailable on {platform.system()}/{platform.machine()}. "
            f"Prebuilt wheels exist for: {', '.join(SUPPORTED_WHEELS)}. "
            f"Cause: {exc}"
        ) from exc

    _declare_twins(ffi)
    _verify_symbols(lib)
    _verify_layouts(ffi)
    return Native(ffi=ffi, lib=lib)


def _declare_twins(ffi: Any) -> None:
    """Add our struct twin to pyghostty's shared FFI, tolerating a future upstream fix."""
    try:
        ffi.cdef(_SCROLL_TWIN_CDEF)
    except Exception:
        # Already declared -- either by a re-entrant call (load() is cached, but
        # a caller may hold a stale ffi) or by a pyghostty release that finally
        # shipped the twin. Layout verification below is the real gate.
        pass


def _verify_symbols(lib: Any) -> None:
    missing = []
    for name in REQUIRED_SYMBOLS:
        try:
            getattr(lib, name)
        except AttributeError:
            missing.append(name)
    if missing:
        raise GhosttyUnavailable(
            "pyghostty's ABI is missing symbols ghostty-textual requires "
            f"({len(missing)}): {', '.join(missing)}. "
            "This build of pyghostty is not the pinned one."
        )


def _verify_layouts(ffi: Any) -> None:
    for twin, real in _TWIN_PAIRS:
        try:
            got = (ffi.sizeof(twin), ffi.alignof(twin))
            want = (ffi.sizeof(real), ffi.alignof(real))
        except Exception as exc:
            raise GhosttyUnavailable(f"cannot measure {twin}/{real}: {exc}") from exc
        if got != want:
            raise GhosttyUnavailable(
                f"union twin {twin} does not match {real}: "
                f"size/align {got} != {want}. Passing it by value would corrupt "
                f"the stack on this ABI."
            )
