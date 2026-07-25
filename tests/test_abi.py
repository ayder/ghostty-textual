"""ABI verification: required symbols, union twins, and twin invocation.

Spec v2 §2, §7. These guard the pinned `pyghostty==0.1.0` private ABI. If any of
them fail after a dependency bump, the bump is not safe -- that is the point.
"""

from __future__ import annotations

import pytest

from ghostty_textual._native import (
    _TWIN_PAIRS,
    REQUIRED_SYMBOLS,
    SUPPORTED_WHEELS,
    GhosttyUnavailable,
    Native,
    load,
)


def test_load_is_cached() -> None:
    assert load() is load()


def test_all_required_symbols_present(native: Native) -> None:
    missing = [name for name in REQUIRED_SYMBOLS if not hasattr(native.lib, name)]
    assert missing == []


@pytest.mark.parametrize(("twin", "real"), _TWIN_PAIRS)
def test_union_twin_layout_matches(native: Native, twin: str, real: str) -> None:
    """A twin whose layout diverges would corrupt the stack when passed by value."""
    ffi = native.ffi
    assert (ffi.sizeof(twin), ffi.alignof(twin)) == (ffi.sizeof(real), ffi.alignof(real))


def test_scroll_viewport_union_is_passed_indirectly(native: Native) -> None:
    """24 bytes: over the 16-byte threshold on both arm64 AAPCS64 and x86-64 SysV.

    This is *why* the struct-twin trick is sound. If libghostty ever shrinks the
    union below the threshold, the calling convention changes and the twin stops
    being equivalent -- so pin the size.
    """
    assert native.ffi.sizeof("GhosttyTerminalScrollViewport") == 24


def test_scroll_viewport_twin_is_callable(native: Native) -> None:
    """The blocker from spec v2 §2: ABI-mode cffi cannot call this without a twin."""
    from tests.conftest import make_terminal

    harness = make_terminal(native, rows=5, scrollback=100)
    try:
        harness.feed(b"".join(b"line%d\r\n" % i for i in range(40)))
        assert harness.get_u32("SCROLLBACK_ROWS") > 0

        native.scroll_viewport(harness.terminal, native.lib.GHOSTTY_SCROLL_VIEWPORT_TOP)
        at_top = harness.get_u32("VIEWPORT_ACTIVE")

        native.scroll_viewport(harness.terminal, native.lib.GHOSTTY_SCROLL_VIEWPORT_BOTTOM)
        at_bottom = harness.get_u32("VIEWPORT_ACTIVE")

        assert at_top != at_bottom, "scrolling to top and bottom gave the same viewport"
    finally:
        harness.close()


def test_unavailable_is_raised_at_construction_not_import() -> None:
    """Spec v2 §7: importing the package must work on platforms with no wheel."""
    import ghostty_textual

    assert ghostty_textual.GhosttyUnavailable is GhosttyUnavailable
    assert issubclass(GhosttyUnavailable, RuntimeError)


def test_supported_wheel_tags_are_complete() -> None:
    """Linux tags are dual-tagged; shortening them misreports what we support."""
    assert SUPPORTED_WHEELS == (
        "macosx_13_0_arm64",
        "macosx_13_0_x86_64",
        "manylinux_2_27_aarch64.manylinux_2_28_aarch64",
        "manylinux_2_27_x86_64.manylinux_2_28_x86_64",
    )
