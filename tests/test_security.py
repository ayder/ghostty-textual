from __future__ import annotations

from ghostty_textual.emulator import (
    ClipboardPolicy,
    ClipboardWritten,
    ResourceLimits,
    Terminal,
)


def test_clipboard_policy_is_opt_in_and_bounded() -> None:
    sequence = b"\x1b]52;c;aGk=\x07"
    with Terminal(20, 3) as terminal:
        assert terminal.feed(sequence).notifications == ()
    with Terminal(20, 3, clipboard=ClipboardPolicy(True, 10)) as terminal:
        assert ClipboardWritten("hi") in terminal.feed(sequence).notifications
    with Terminal(20, 3, clipboard=ClipboardPolicy(True, 1)) as terminal:
        assert terminal.feed(sequence).notifications == ()


def test_link_limits_degrade_to_unlinked_cells() -> None:
    limits = ResourceLimits(max_interned_links=1, max_link_uri_bytes=32)
    with Terminal(20, 2, limits=limits) as terminal:
        terminal.feed(
            b"\x1b]8;;https://a.co\x1b\\A\x1b]8;;\x1b\\"
            b"\x1b]8;;https://b.co\x1b\\B\x1b]8;;\x1b\\"
            b"\x1b]8;;https://example.com/too-long\x1b\\C\x1b]8;;\x1b\\"
        )
        frame = terminal.snapshot()
        cells = frame.row_patches[0].cells
        assert cells[0].link_id == 0
        assert cells[1].link_id is None
        assert cells[2].link_id is None
        assert frame.links == ("https://a.co",)


def test_notification_flood_is_rate_limited() -> None:
    with Terminal(20, 3, limits=ResourceLimits(notification_rate_per_sec=3)) as terminal:
        assert len(terminal.feed(b"\x07" * 100).notifications) == 3
