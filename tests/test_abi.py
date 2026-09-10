"""ABI verification: required symbols, union twins, and twin invocation.

Spec v2 §2, §7. These guard the pinned `pyghostty==0.1.1` private ABI. If any of
them fail after a dependency bump, the bump is not safe -- that is the point.
"""

from __future__ import annotations

import builtins
import re
from pathlib import Path

import pytest

from ghostty_textual._native import (
    _TWIN_PAIRS,
    REQUIRED_SYMBOLS,
    SUPPORTED_WHEELS,
    GhosttyUnavailable,
    Native,
    _verify_layouts,
    _verify_symbols,
    load,
)


def test_load_is_cached() -> None:
    assert load() is load()


def test_all_required_symbols_present(native: Native) -> None:
    missing = [name for name in REQUIRED_SYMBOLS if not hasattr(native.lib, name)]
    assert missing == []


def test_every_native_call_site_is_declared_required() -> None:
    """A new call must extend startup verification in the same change."""
    source_root = Path(__file__).parents[1] / "src" / "ghostty_textual"
    called: set[str] = set()
    for name in ("_native.py", "_render.py", "emulator.py"):
        source = (source_root / name).read_text()
        called.update(re.findall(r"\bghostty_[a-z0-9_]+", source))
    called.discard("ghostty_textual")
    assert called <= set(REQUIRED_SYMBOLS), sorted(called - set(REQUIRED_SYMBOLS))


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


def test_missing_native_library_reports_supported_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "pyghostty._ffi":
            raise ImportError("simulated unsupported platform")
        return real_import(name, *args, **kwargs)

    load.cache_clear()
    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(GhosttyUnavailable, match="Prebuilt wheels exist for"):
        load()
    load.cache_clear()


def test_corrupt_library_and_layout_fail_at_the_native_boundary() -> None:
    with pytest.raises(GhosttyUnavailable, match="missing symbols"):
        _verify_symbols(object())

    class MismatchedFfi:
        @staticmethod
        def sizeof(name: str) -> int:
            return 24 if name.endswith("S") else 16

        @staticmethod
        def alignof(name: str) -> int:
            return 8

    with pytest.raises(GhosttyUnavailable, match="does not match"):
        _verify_layouts(MismatchedFfi())
