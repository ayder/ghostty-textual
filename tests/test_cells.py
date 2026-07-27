from __future__ import annotations

import pytest

from ghostty_textual.cells import CellStyle, InternerFull, LinkInterner, StyleInterner


def test_style_interner_is_stable_and_bounded() -> None:
    red = CellStyle(fg=(255, 0, 0))
    interner = StyleInterner(limit=1)
    assert interner.intern(red) == interner.intern(red) == 0
    with pytest.raises(InternerFull):
        interner.intern(CellStyle(fg=(0, 0, 255)))
    assert interner.table() == (red,)


def test_style_rollover_invalidates_ids() -> None:
    interner = StyleInterner()
    interner.intern(CellStyle())
    interner.rollover()
    assert interner.generation == 1
    with pytest.raises(KeyError):
        interner.resolve(0)


def test_negative_ids_are_rejected() -> None:
    interner = StyleInterner()
    interner.intern(CellStyle())
    with pytest.raises(KeyError):
        interner.resolve(-1)


def test_link_overflow_and_long_uri_degrade() -> None:
    links = LinkInterner(limit=1, max_uri_bytes=8)
    assert links.intern("short") == 0
    assert links.intern("second") is None
    assert links.intern("too-long-uri") is None


def test_link_utf8_limit_is_in_bytes() -> None:
    links = LinkInterner(limit=2, max_uri_bytes=3)
    assert links.intern("界") == 0
    assert links.intern("界a") is None
