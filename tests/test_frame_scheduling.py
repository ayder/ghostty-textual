from __future__ import annotations

import asyncio

import pytest

from ghostty_textual import GhosttyError
from ghostty_textual.emulator import Terminal
from ghostty_textual.widget import TitleChanged
from tests.widget_harness import Harness


async def test_feed_burst_coalesces_frames_but_dispatches_effects_immediately(monkeypatch) -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)) as pilot:
        original = app.view.terminal.snapshot
        calls = []

        def snapshot(**kwargs):
            calls.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(app.view.terminal, "snapshot", snapshot)
        posted = []
        original_post = app.view.post_message

        def post(message):
            posted.append(message)
            return original_post(message)

        monkeypatch.setattr(app.view, "post_message", post)
        app.view.feed(b"a\x1b[6n\x1b]0;first\x07")
        app.view.feed(b"b\x1b[6n\x1b]0;second\x07")
        assert calls == []
        assert [message.title for message in posted if isinstance(message, TitleChanged)] == [
            "first",
            "second",
        ]
        assert app.view._queue.qsize() == 2
        await pilot.pause()
        assert len(calls) == 1
        assert app.view.render_line(0).text.startswith("ab")
        assert app.sent == [b"\x1b[1;2R", b"\x1b[1;3R"]


async def test_explicit_refresh_consumes_pending_frame(monkeypatch) -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)) as pilot:
        app.view.feed(b"now")
        app.view.refresh_frame()
        assert app.view.render_line(0).text.startswith("now")

        def unexpected(**kwargs):
            pytest.fail("explicit refresh left a scheduled snapshot")

        monkeypatch.setattr(app.view.terminal, "snapshot", unexpected)
        await pilot.pause()


@pytest.mark.parametrize("action", ["hard_reset", "reset_io", "failure", "unmount"])
async def test_pending_frame_is_cancelled_at_lifecycle_boundary(action, monkeypatch) -> None:
    with Terminal(20, 3) as terminal:
        app = Harness(terminal=terminal)
        async with app.run_test(size=(20, 3)) as pilot:
            original = app.view.terminal.snapshot
            calls = []

            def snapshot(**kwargs):
                calls.append(kwargs)
                return original(**kwargs)

            monkeypatch.setattr(app.view.terminal, "snapshot", snapshot)
            app.view.feed(b"old")
            if action == "hard_reset":
                app.view.hard_reset()
                assert len(calls) == 1  # The reset itself renders synchronously.
                assert "old" not in app.view.render_line(0).text
            elif action == "reset_io":
                await app.view.reset_io()
            elif action == "failure":
                app.view._fail(GhosttyError("test failure"))
            else:
                # Enter the lifecycle hook before yielding to the queued callback.
                await app.view.on_unmount()
            expected_calls = 1 if action == "hard_reset" else 0
            await pilot.pause()
            assert len(calls) == expected_calls
            assert app.view._frame_handle is None


async def test_sustained_bursts_yield_and_keep_up_with_output(monkeypatch) -> None:
    app = Harness()
    async with app.run_test(size=(40, 3)):
        original = app.view.terminal.snapshot
        calls = 0

        def snapshot(**kwargs):
            nonlocal calls
            calls += 1
            return original(**kwargs)

        monkeypatch.setattr(app.view.terminal, "snapshot", snapshot)
        for index in range(5):
            for _ in range(3):
                app.view.feed(b"x")
            await asyncio.sleep(0)
            assert app.view.render_line(0).text.startswith("x" * ((index + 1) * 3))
        assert calls == 5


async def test_sync_release_cancels_timeout_and_displays_latest_frame() -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)) as pilot:
        app.view.feed(b"\x1b[?2026hfirst")
        await asyncio.sleep(0)
        assert "first" not in app.view.render_line(0).text
        app.view.feed(b"second\x1b[?2026l")
        assert app.view._sync_timeout_timer is None
        await pilot.pause()
        assert app.view.render_line(0).text.startswith("firstsecond")


async def test_scheduled_snapshot_failure_is_contained(monkeypatch) -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)) as pilot:

        def fail(**kwargs):
            raise GhosttyError("snapshot failed")

        monkeypatch.setattr(app.view.terminal, "snapshot", fail)
        app.view.feed(b"x")
        await pilot.pause()
        assert app.view.failed
        assert app.view._frame_handle is None
        assert "snapshot failed" in app.view.render_line(0).text
