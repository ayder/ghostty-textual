from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import pytest
from textual import events

from ghostty_textual.widget import TerminalFailed, TerminalView
from tests.widget_harness import Harness


def _input(view: TerminalView, kind: str) -> Awaitable[None]:
    if kind == "key":
        return view.on_key(events.Key("a", "a"))
    if kind == "paste":
        return view.on_paste(events.Paste("pasted"))
    if kind == "focus":
        return view.on_focus(events.Focus())
    return view.on_blur(events.Blur())


async def _fill_queue(app: Harness) -> None:
    # The writer takes the first item, then waits on the harness's send gate.
    app.view._enqueue_nowait(b"first")
    await asyncio.sleep(0)
    app.view._enqueue_nowait(b"second")


async def _block_input(view: TerminalView, kind: str) -> asyncio.Task[None]:
    pending = asyncio.create_task(_input(view, kind))
    # Let the handler and its queue helpers reach their blocking awaits.
    for _ in range(4):
        await asyncio.sleep(0)
    assert not pending.done()
    return pending


@pytest.mark.parametrize("kind", ["key", "paste", "focus", "blur"])
async def test_reconnect_discards_blocked_input_without_failing_new_writer(kind: str) -> None:
    app = Harness(stalled_send=True, queue_size=1)
    async with app.run_test(size=(20, 3)) as pilot:
        app.view.feed(b"\x1b[?1004h")
        await _fill_queue(app)
        pending = await _block_input(app.view, kind)

        await app.view.reset_io()
        app.view.hard_reset()
        await asyncio.wait_for(pending, timeout=1)
        app.release_send()
        await app.view.on_key(events.Key("b", "b"))
        await pilot.pause()

        assert not app.view.failed, str(app.view._failure)
        assert app.view._writer_task is not None
        assert app.sent == [b"b"]
        assert app.messages_of(TerminalFailed) == []


@pytest.mark.parametrize("kind", ["key", "paste", "focus", "blur"])
async def test_cancelling_blocked_input_does_not_send_it_or_leave_helpers(kind: str) -> None:
    app = Harness(stalled_send=True, queue_size=1)
    async with app.run_test(size=(20, 3)) as pilot:
        app.view.feed(b"\x1b[?1004h")
        await _fill_queue(app)
        before = asyncio.all_tasks()
        pending = await _block_input(app.view, kind)
        # Capture queue/event waiters without depending on their task names.
        helpers = asyncio.all_tasks() - before - {pending}

        pending.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await pending
            helpers_finished = all(task.done() for task in helpers)
        finally:
            # A regression must not also stall Textual's unmount/focus events.
            app.release_send()
        await app.view.on_key(events.Key("b", "b"))
        await pilot.pause()

        assert app.sent == [b"first", b"second", b"b"]
        assert helpers_finished, "cancelled input left pending tasks"
        assert not app.view.failed


async def test_blocked_input_preserves_fifo_when_transport_resumes() -> None:
    app = Harness(stalled_send=True, queue_size=1)
    async with app.run_test(size=(20, 3)) as pilot:
        await _fill_queue(app)
        pending = await _block_input(app.view, "key")

        app.release_send()
        await asyncio.wait_for(pending, timeout=1)
        await pilot.pause()

        assert app.sent == [b"first", b"second", b"a"]
        assert not app.view.failed


async def test_transport_failure_releases_blocked_input_and_reports_once() -> None:
    app = Harness(stalled_send=True, queue_size=1)
    async with app.run_test(size=(20, 3)) as pilot:
        async def fail_send(data: bytes) -> None:
            await app._gate.wait()
            raise OSError("connection lost")

        app.view._send = fail_send
        await _fill_queue(app)
        pending = await _block_input(app.view, "key")

        app.release_send()
        await asyncio.wait_for(pending, timeout=1)
        await pilot.pause()

        assert app.view.failed
        failures = app.messages_of(TerminalFailed)
        assert len(failures) == 1
        assert "connection lost" in str(failures[0].error)
        assert app.sent == []
