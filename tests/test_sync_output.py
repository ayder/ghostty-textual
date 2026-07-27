from __future__ import annotations

import time

from ghostty_textual.emulator import Terminal


def test_synchronized_output_withholds_then_times_out() -> None:
    with Terminal(20, 3) as terminal:
        terminal.snapshot(force=True)
        terminal.feed(b"\x1b[?2026hhello")
        assert terminal.modes.sync_output
        assert terminal.snapshot() is None
        assert terminal.snapshot(force=True).frame_pending

        terminal.feed(b"world")
        time.sleep(0.16)
        frame = terminal.snapshot()
        assert frame is not None and frame.frame_pending


def test_synchronized_output_end_releases_frame() -> None:
    with Terminal(20, 3) as terminal:
        terminal.snapshot(force=True)
        terminal.feed(b"\x1b[?2026hhello\x1b[?2026l")
        assert not terminal.modes.sync_output
        assert terminal.snapshot() is not None
