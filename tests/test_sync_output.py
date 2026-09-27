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


def test_suppressed_snapshot_does_not_extract_and_preserves_pending_changes(monkeypatch) -> None:
    with Terminal(20, 3) as terminal:
        terminal.feed(b"\x1b[31mold")
        terminal.snapshot()
        original = terminal._render.update
        calls = []

        def update(handle):
            calls.append(handle)
            return original(handle)

        monkeypatch.setattr(terminal._render, "update", update)
        terminal.feed(b"\x1b[?2026h\x1b[1;1Hnew\x1b]4;1;#123456\x07")
        assert terminal.snapshot() is None
        assert calls == []
        forced = terminal.snapshot(force=True)
        assert forced.frame_pending
        assert len(calls) == 1
        terminal.feed(b"\x1b[1;1Hend\x1b[?2026l")
        frame = terminal.snapshot()
        assert not frame.frame_pending
        cells = frame.row_patches[0].cells
        assert "".join(cell.text for cell in cells[:3]) == "end"
        assert frame.styles[cells[0].style_id].fg == (18, 52, 86)
