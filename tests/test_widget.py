from __future__ import annotations

from types import SimpleNamespace

from textual import events
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.geometry import Offset, Region
from textual.selection import Selection

from ghostty_textual import (
    Cell,
    CellStyle,
    Frame,
    GhosttyError,
    RowPatch,
)
from ghostty_textual.emulator import Terminal
from ghostty_textual.widget import MouseMode, PasteRejected, Resized, TerminalFailed, TerminalView
from tests.widget_harness import Harness


async def test_render_sizing_and_ordered_query_reply() -> None:
    seen: list[tuple[int, int]] = []
    app = Harness(resize_transport=lambda cols, rows: seen.append((cols, rows)))
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"hello\x1b[6n")
        await pilot.pause()
        assert "hello" in app.view.render_line(0).text
        assert app.sent == [b"\x1b[1;6R"]
        assert seen[0] == (40, 10)
        assert app.view.terminal.cols == 40
        assert app.messages_of(Resized)
        assert app.view.render_line(0)._segments[0].style.meta["offset"] == (0, 0)


async def test_injected_terminal_ownership() -> None:
    terminal = Terminal(40, 10)
    app = Harness(terminal=terminal)
    async with app.run_test(size=(40, 10)):
        pass
    assert not terminal.closed
    terminal.close()


async def test_transferred_terminal_ownership() -> None:
    terminal = Terminal(40, 10)
    app = Harness(terminal=terminal, close_terminal=True)
    async with app.run_test(size=(40, 10)):
        pass
    assert terminal.closed


async def test_local_wheel_never_sends_mouse_bytes() -> None:
    app = Harness()
    async with app.run_test(size=(40, 5)) as pilot:
        app.view.feed(b"".join(b"line%d\r\n" % index for index in range(40)))
        await pilot.hover(app.view)
        app.view.post_message(events.MouseScrollUp(app.view, 0, 0, 0, -1, 0, False, False, False))
        await pilot.pause()
        assert app.view.mouse_mode is MouseMode.LOCAL
        assert app.sent == []
        assert not app.view.terminal.viewport.at_bottom


async def test_reserved_key_is_not_sent() -> None:
    app = Harness(reserved_keys=frozenset({"ctrl+w"}))
    async with app.run_test(size=(40, 10)) as pilot:
        await pilot.press("ctrl+w")
        await pilot.pause()
        assert app.sent == []


async def test_failure_is_contained_and_visible() -> None:
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        original = app.view.terminal.feed

        def fail(data: bytes):
            from ghostty_textual import GhosttyError

            raise GhosttyError("broken")

        app.view.terminal.feed = fail
        app.view.feed(b"x")
        app.view.feed(b"ignored")
        await pilot.pause()
        assert app.view.failed
        assert app.messages_of(TerminalFailed)
        assert "Terminal failed" in app.view.render_line(0).text
        app.view.terminal.feed = original


async def test_reset_io_discards_queued_bytes() -> None:
    app = Harness(stalled_send=True)
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b[6n")
        await pilot.pause()
        await app.view.reset_io()
        app.release_send()
        await pilot.pause()
        assert app.sent == []


async def test_unterminated_synchronized_output_is_rendered_after_timeout() -> None:
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.feed(b"\x1b[?2026hhello")
        assert "hello" not in app.view.render_line(0).text
        await pilot.pause(0.2)
        assert "hello" in app.view.render_line(0).text


class DynamicMountApp(App):
    def __init__(self) -> None:
        super().__init__()
        self.sent: list[bytes] = []

    def compose(self) -> ComposeResult:
        yield Container(id="host")

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


async def test_dynamic_mount_applies_size_before_mount_returns() -> None:
    seen: list[tuple[int, int]] = []
    app = DynamicMountApp()
    async with app.run_test(size=(100, 30)):
        view = TerminalView(
            send=app.send,
            resize_transport=lambda cols, rows: seen.append((cols, rows)),
        )
        await app.query_one("#host", Container).mount(view)
        assert seen == [(100, 30)]
        assert (view.terminal.cols, view.terminal.rows) == (100, 30)


async def test_resize_error_is_contained() -> None:
    app = Harness()
    async with app.run_test(size=(40, 10)):
        app.view._last_size = None

        def fail_resize(cols: int, rows: int) -> None:
            raise GhosttyError("resize failed")

        app.view.terminal.resize = fail_resize
        app.view.sync_terminal_size()
        assert app.view.failed


async def test_failed_view_shuts_queue_and_ignores_focus() -> None:
    app = Harness(queue_size=1)
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.terminal.feed(b"\x1b[?1004h")
        app.view._fail(GhosttyError("failed"))
        app.view.post_message(events.Focus())
        await pilot.pause()
        assert app.view._queue is None
        assert app.view._writer_task is None


async def test_spacer_head_paints_right_margin_background() -> None:
    app = Harness()
    async with app.run_test(size=(6, 2)):
        app.view.feed(b"\x1b[44mabcde" + "字".encode())
        strip = app.view.render_line(0)
        assert strip.cell_length == 6
        assert strip.text == "abcde "
        assert strip._segments[-1].style.bgcolor is not None


async def test_wide_tail_cursor_overlays_wide_head() -> None:
    app = Harness()
    async with app.run_test(size=(6, 2)):
        app.view.feed("界".encode())
        app.view.feed(b"\x1b[1;2H")
        app.view._focused = True
        strip = app.view.render_line(0)
        assert strip._segments[0].style.reverse


async def test_selection_preserves_soft_wraps() -> None:
    app = Harness()
    async with app.run_test(size=(10, 3)):
        app.view.feed(b"ABCDEFGHIJKLMNOPQRST")
        app.view._selection_anchor = (0, 0)
        app.view._selection_end = (9, 1)
        assert app.view.get_selection() == "ABCDEFGHIJKLMNOPQRST"


async def test_selection_implements_textual_widget_contract() -> None:
    app = Harness()
    async with app.run_test(size=(10, 3)):
        app.view.feed(b"abc\r\ndef")
        selection = Selection(Offset(1, 0), Offset(2, 1))
        app.screen.selections[app.view] = selection

        assert app.view.get_selection(selection) == ("bc\nde", "\n")
        assert app.screen.get_selected_text() == "bc\nde"


async def test_unsafe_paste_posts_notification() -> None:
    app = Harness()
    async with app.run_test(size=(40, 10)) as pilot:
        app.view.post_message(events.Paste("unsafe\n"))
        await pilot.pause()
        messages = app.messages_of(PasteRejected)
        assert messages and messages[0].text == "unsafe\n"
        assert app.sent == []


async def test_cursor_only_frame_invalidates_only_cursor_row() -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)):
        app.view.feed(b"abcdef")
        calls: list[tuple[Region, ...]] = []

        def record_refresh(*regions: Region, **kwargs) -> None:
            calls.append(regions)

        app.view.refresh = record_refresh
        app.view.feed(b"\x1b[3D")
        assert calls == [(Region(0, 0, 20, 1),)]
        assert app.view._cursor.x == 3


async def test_generation_mismatch_recovers_with_a_full_native_frame() -> None:
    app = Harness()
    async with app.run_test(size=(10, 2)):
        app.view.feed(b"real")
        shadow = app.view._shadow
        fake = Frame(
            generation=shadow.generation + 1,
            cols=10,
            rows=2,
            full_redraw=False,
            row_patches=(RowPatch(0, (Cell("X", 1, 0),)),),
            styles=(CellStyle(),),
            links=(),
            cursor=app.view._cursor,
            viewport=app.view.terminal.viewport,
            frame_pending=False,
        )

        app.view._apply_frame(fake)

        assert app.view._shadow.generation == shadow.generation
        assert app.view._shadow.rows[0][0].text == "r"


async def test_widget_hard_reset_clears_remote_state() -> None:
    app = Harness()
    async with app.run_test(size=(20, 3)):
        app.view.feed(b"\x1b]0;old\x07visible\x1b[?1049h")
        app.view.hard_reset()
        assert not app.view.failed
        assert app.view.terminal.title in (None, "")
        assert not app.view.terminal.modes.alt_screen
        assert "visible" not in app.view.render_line(0).text


async def test_drag_selection_preserves_hard_breaks() -> None:
    app = Harness()
    async with app.run_test(size=(10, 3)):
        app.view.feed(b"abc\r\ndef")
        app.view.on_mouse_down(SimpleNamespace(x=1, y=0))
        app.view.on_mouse_move(SimpleNamespace(x=2, y=1, button=1))
        app.view.on_mouse_up(SimpleNamespace(x=2, y=1))
        assert app.view.get_selection() == "bc\ndef"


async def test_reserved_key_handler_leaves_event_unstopped() -> None:
    class ReservedEvent:
        key = "ctrl+w"
        character = None

        def __init__(self) -> None:
            self.stopped = False
            self.prevented = False

        def stop(self) -> None:
            self.stopped = True

        def prevent_default(self) -> None:
            self.prevented = True

    app = Harness(reserved_keys=frozenset({"ctrl+w"}))
    async with app.run_test(size=(20, 3)):
        event = ReservedEvent()
        await app.view.on_key(event)
        assert not event.stopped
        assert not event.prevented
        assert app.sent == []
