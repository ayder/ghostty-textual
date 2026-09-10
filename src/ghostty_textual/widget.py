"""A Textual widget backed by the transport-neutral :class:`Terminal`."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from rich.segment import Segment
from rich.style import Style
from textual import events
from textual.geometry import Region
from textual.message import Message
from textual.selection import Selection
from textual.strip import Strip
from textual.widget import Widget

from ghostty_textual._native import GhosttyError
from ghostty_textual.cells import Cell, CellStyle
from ghostty_textual.emulator import (
    BellRang,
    ClipboardWritten,
    Frame,
    ScrollDelta,
    Terminal,
)
from ghostty_textual.emulator import (
    TitleChanged as EmulatorTitleChanged,
)
from ghostty_textual.keys import from_textual
from ghostty_textual.theme import TerminalTheme


class MouseMode(Enum):
    LOCAL = "local"


class Resized(Message, namespace="terminal_view"):
    def __init__(self, cols: int, rows: int) -> None:
        self.cols, self.rows = cols, rows
        super().__init__()


class TitleChanged(Message, namespace="terminal_view"):
    def __init__(self, title: str) -> None:
        self.title = title
        super().__init__()


class Bell(Message, namespace="terminal_view"):
    pass


class LinkClicked(Message, namespace="terminal_view"):
    def __init__(self, uri: str) -> None:
        self.uri = uri
        super().__init__()


class ClipboardWrite(Message, namespace="terminal_view"):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


class TerminalFailed(Message, namespace="terminal_view"):
    def __init__(self, error: GhosttyError) -> None:
        self.error = error
        super().__init__()


class PasteRejected(Message, namespace="terminal_view"):
    def __init__(self, text: str) -> None:
        self.text = text
        super().__init__()


@dataclass(frozen=True, slots=True)
class _Shadow:
    generation: int
    rows: tuple[tuple[Cell, ...], ...]
    wraps: tuple[bool, ...]
    styles: tuple[CellStyle, ...]
    links: tuple[str, ...]


class TerminalView(Widget):
    """Render a current Ghostty viewport and serialize all transport writes."""

    DEFAULT_CSS = """
    TerminalView {
        width: 1fr;
        height: 1fr;
        overflow: hidden hidden;
    }
    """
    can_focus = True
    mouse_mode = MouseMode.LOCAL

    Resized = Resized
    TitleChanged = TitleChanged
    Bell = Bell
    LinkClicked = LinkClicked
    ClipboardWrite = ClipboardWrite
    TerminalFailed = TerminalFailed
    PasteRejected = PasteRejected

    def __init__(
        self,
        *,
        send: Callable[[bytes], Awaitable[None]],
        resize_transport: Callable[[int, int], None] | None = None,
        scrollback: int = 5000,
        theme: TerminalTheme | None = None,
        reserved_keys: frozenset[str] = frozenset(),
        terminal: Terminal | None = None,
        close_terminal: bool = False,
        queue_size: int = 256,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._send = send
        self._resize_transport = resize_transport
        self._reserved_keys = reserved_keys
        self._queue_size = queue_size
        self._queue: asyncio.Queue[bytes] | None = None
        self._queue_shutdown: asyncio.Event | None = None
        self._writer_task: asyncio.Task[None] | None = None
        self._writer_generation = 0
        self._mounted_ready = False
        self._owns_terminal = terminal is None or close_terminal
        self.terminal = terminal or Terminal(80, 24, scrollback=scrollback, theme=theme)
        self._initial_size = (self.terminal.cols, self.terminal.rows)
        self._shadow = _Shadow(-1, (), (), (), ())
        self._cursor = None
        self._last_size: tuple[int, int] | None = None
        self.failed = False
        self._failure: GhosttyError | None = None
        self._selection_anchor: tuple[int, int] | None = None
        self._selection_end: tuple[int, int] | None = None
        self._focused = False
        self._sync_timeout_timer: Any = None
        self._blink_timer: Any = None
        self._cursor_phase = True

    @property
    def cursor_visible(self) -> bool:
        return bool(self._cursor and self._cursor.visible)

    def on_mount(self) -> None:
        self._mounted_ready = True
        self._start_writer()
        self._blink_timer = self.set_interval(0.5, self._blink_cursor)
        self.sync_terminal_size()
        self.refresh_frame(force=True)

    async def on_unmount(self) -> None:
        self._mounted_ready = False
        if self._blink_timer is not None:
            self._blink_timer.stop()
            self._blink_timer = None
        await self.reset_io()
        if self._owns_terminal:
            self.terminal.close()

    def sync_terminal_size(self) -> tuple[int, int]:
        size = self.content_size
        cols, rows = int(size.width), int(size.height)
        if (cols <= 0 or rows <= 0) and self.parent is not None:
            parent_size = self.parent.content_size
            cols, rows = int(parent_size.width), int(parent_size.height)
        if cols <= 0 or rows <= 0:
            return self._last_size or self._initial_size
        current = (cols, rows)
        if current == self._last_size:
            return current
        try:
            self.terminal.resize(cols, rows)
            if self._resize_transport is not None:
                self._resize_transport(cols, rows)
            self._last_size = current
            self.post_message(Resized(cols, rows))
            self.refresh_frame()
        except GhosttyError as exc:
            self._fail(exc)
            return self._last_size or self._initial_size
        return current

    def on_resize(self, event: events.Resize) -> None:  # noqa: ARG002
        if self.failed:
            return
        self.sync_terminal_size()

    def feed(self, data: bytes) -> None:
        if self.failed or not self._mounted_ready:
            return
        try:
            effects = self.terminal.feed(data)
            for write in effects.pty_writes:
                self._enqueue_nowait(write)
            for notification in effects.notifications:
                if isinstance(notification, EmulatorTitleChanged):
                    self.post_message(TitleChanged(notification.title))
                elif isinstance(notification, BellRang):
                    self.post_message(Bell())
                elif isinstance(notification, ClipboardWritten):
                    self.post_message(ClipboardWrite(notification.text))
            self.refresh_frame()
            if self.terminal.modes.sync_output:
                if self._sync_timeout_timer is None:
                    self._sync_timeout_timer = self.set_timer(0.16, self._flush_sync_timeout)
            elif self._sync_timeout_timer is not None:
                self._sync_timeout_timer.stop()
                self._sync_timeout_timer = None
        except (GhosttyError, asyncio.QueueFull) as exc:
            error = exc if isinstance(exc, GhosttyError) else GhosttyError("writer queue is full")
            self._fail(error)

    def _flush_sync_timeout(self) -> None:
        self._sync_timeout_timer = None
        self.refresh_frame()

    def refresh_frame(self, *, force: bool = False) -> None:
        if self.failed:
            return
        try:
            frame = self.terminal.snapshot(force=force)
            if frame is None:
                return
            changed_rows = self._apply_frame(frame)
            self._refresh_rows(changed_rows)
        except GhosttyError as exc:
            self._fail(exc)

    def _apply_frame(self, frame: Frame) -> set[int]:
        old_cursor = self._cursor
        if frame.full_redraw or frame.generation != self._shadow.generation:
            if not frame.full_redraw and self._shadow.generation >= 0:
                recovery = self.terminal.snapshot(force=True)
                if recovery is None:
                    return set()
                frame = recovery
            rows: list[tuple[Cell, ...]] = [() for _ in range(frame.rows)]
            wraps = [False for _ in range(frame.rows)]
            changed_rows = set(range(frame.rows))
        else:
            rows = list(self._shadow.rows)
            wraps = list(self._shadow.wraps)
            changed_rows: set[int] = set()
            if len(rows) != frame.rows:
                rows = [() for _ in range(frame.rows)]
                wraps = [False for _ in range(frame.rows)]
                changed_rows.update(range(frame.rows))
        for patch in frame.row_patches:
            if 0 <= patch.y < len(rows):
                rows[patch.y] = patch.cells
                wraps[patch.y] = patch.wrapped
                changed_rows.add(patch.y)
        for cursor in (old_cursor, frame.cursor):
            if cursor is not None and cursor.visible and 0 <= cursor.y < frame.rows:
                changed_rows.add(cursor.y)
        self._shadow = _Shadow(
            frame.generation,
            tuple(rows),
            tuple(wraps),
            frame.styles,
            frame.links,
        )
        self._cursor = frame.cursor
        return changed_rows

    def _refresh_rows(self, rows: set[int]) -> None:
        if not rows:
            return
        width = max(self._last_size[0] if self._last_size else 0, self.size.width, 1)
        self.refresh(*(Region(0, y, width, 1) for y in sorted(rows)))

    def render_line(self, y: int) -> Strip:
        if self.failed:
            text = "Terminal failed"
            if self._failure is not None:
                text = f"{text}: {self._failure}"
            return Strip([Segment(text, Style(color="white", bgcolor="red"))]).apply_offsets(0, y)
        if not 0 <= y < len(self._shadow.rows):
            return Strip.blank(self.size.width).apply_offsets(0, y)
        cells = self._shadow.rows[y]
        segments: list[Segment] = []
        run_text: list[str] = []
        run_key: tuple[int, str | None] | None = None

        def flush() -> None:
            if not run_text or run_key is None:
                return
            style = self._rich_style(self._shadow.styles[run_key[0]])
            if run_key[1] == "underline":
                style += Style(underline=True)
            elif run_key[1] == "reverse":
                style += Style(reverse=True)
            segments.append(Segment("".join(run_text), style))
            run_text.clear()

        for x, cell in enumerate(cells):
            if cell.width == 0:
                continue
            text = cell.text or " "
            overlay = self._cell_overlay(x, y)
            key = (cell.style_id, overlay)
            if key != run_key:
                flush()
                run_key = key
            run_text.append(text)
        flush()
        strip = Strip(segments, cell_length=self.terminal.cols)
        return strip.apply_offsets(0, y)

    def _cell_overlay(self, x: int, y: int) -> str | None:
        if (
            self._focused
            and self._cursor is not None
            and self._cursor.visible
            and (not self._cursor.blinking or self._cursor_phase)
            and (
                self._cursor.x - (1 if self._cursor.wide_tail else 0),
                self._cursor.y,
            )
            == (x, y)
        ):
            return "underline" if self._cursor.style == 2 else "reverse"
        if self._selection_anchor is not None and self._selection_end is not None:
            start, end = sorted(
                (self._selection_anchor, self._selection_end),
                key=lambda point: (point[1], point[0]),
            )
            if (start[1], start[0]) <= (y, x) <= (end[1], end[0]):
                return "reverse"
        return None

    def _blink_cursor(self) -> None:
        if not self.failed and self._focused and self._cursor is not None and self._cursor.blinking:
            self._cursor_phase = not self._cursor_phase
            self._refresh_rows({self._cursor.y})

    @staticmethod
    def _rich_style(style: CellStyle) -> Style:
        def color(value: tuple[int, int, int] | None) -> str | None:
            return None if value is None else f"rgb({value[0]},{value[1]},{value[2]})"

        return Style(
            color=color(style.fg),
            bgcolor=color(style.bg),
            bold=style.bold,
            dim=style.faint,
            italic=style.italic,
            blink=style.blink,
            reverse=style.inverse,
            conceal=style.invisible,
            strike=style.strikethrough,
            overline=style.overline,
            underline=bool(style.underline),
        )

    async def on_key(self, event: events.Key) -> None:
        if self.failed or event.key in self._reserved_keys:
            return
        try:
            if event.key == "shift+pageup":
                self.terminal.scroll_viewport(ScrollDelta(-max(1, self.terminal.rows - 1)))
                self.refresh_frame()
                event.stop()
                return
            if event.key == "shift+pagedown":
                self.terminal.scroll_viewport(ScrollDelta(max(1, self.terminal.rows - 1)))
                self.refresh_frame()
                event.stop()
                return
            key = from_textual(event)
            if key is None:
                return
            encoded = self.terminal.encode_key(key)
            if encoded is not None:
                await self._enqueue(encoded)
                event.stop()
                event.prevent_default()
        except GhosttyError as exc:
            self._fail(exc)

    async def on_paste(self, event: events.Paste) -> None:
        if self.failed:
            return
        try:
            encoded = self.terminal.encode_paste(event.text)
            if encoded is not None:
                await self._enqueue(encoded)
                event.stop()
            else:
                self.post_message(PasteRejected(event.text))
                event.stop()
        except GhosttyError as exc:
            self._fail(exc)

    async def on_focus(self, event: events.Focus) -> None:  # noqa: ARG002
        if self.failed:
            return
        self._focused = True
        self._cursor_phase = True
        if self._cursor is not None:
            self._refresh_rows({self._cursor.y})
        try:
            encoded = self.terminal.encode_focus(True)
            if encoded is not None:
                await self._enqueue(encoded)
        except GhosttyError as exc:
            self._fail(exc)

    async def on_blur(self, event: events.Blur) -> None:  # noqa: ARG002
        if self.failed:
            return
        self._focused = False
        self._cursor_phase = True
        if self._cursor is not None:
            self._refresh_rows({self._cursor.y})
        try:
            encoded = self.terminal.encode_focus(False)
            if encoded is not None:
                await self._enqueue(encoded)
        except GhosttyError as exc:
            self._fail(exc)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        if self.failed:
            return
        try:
            self.terminal.scroll_viewport(ScrollDelta(-3))
            self.refresh_frame()
            event.stop()
        except GhosttyError as exc:
            self._fail(exc)

    def on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        if self.failed:
            return
        try:
            self.terminal.scroll_viewport(ScrollDelta(3))
            self.refresh_frame()
            event.stop()
        except GhosttyError as exc:
            self._fail(exc)

    def on_mouse_down(self, event: events.MouseDown) -> None:
        if self.failed:
            return
        self._selection_anchor = (event.x, event.y)
        self._selection_end = self._selection_anchor
        self._refresh_rows({event.y})

    def on_mouse_move(self, event: events.MouseMove) -> None:
        if self.failed:
            return
        if self._selection_anchor is not None and event.button == 1:
            old_end = self._selection_end
            self._selection_end = (event.x, event.y)
            rows = {event.y}
            if old_end is not None:
                rows.update(range(min(old_end[1], event.y), max(old_end[1], event.y) + 1))
            self._refresh_rows(rows)

    def on_mouse_up(self, event: events.MouseUp) -> None:
        if self.failed:
            return
        if self._selection_anchor is not None:
            self._selection_end = (event.x, event.y)
            self._refresh_rows(
                set(
                    range(
                        min(self._selection_anchor[1], event.y),
                        max(self._selection_anchor[1], event.y) + 1,
                    )
                )
            )

    def on_click(self, event: events.Click) -> None:
        if self.failed:
            return
        cell = self._cell_at(event.x, event.y)
        if cell is not None and cell.link_id is not None:
            if 0 <= cell.link_id < len(self._shadow.links):
                self.post_message(LinkClicked(self._shadow.links[cell.link_id]))

    def get_selection(self, selection: Selection | None = None) -> str | tuple[str, str]:
        """Return custom drag text or satisfy Textual's selection contract."""
        if selection is not None:
            selected_rows: list[tuple[int, tuple[int, int]]] = []
            for y in range(len(self._shadow.rows)):
                if (span := selection.get_span(y)) is not None:
                    selected_rows.append((y, span))
            result: list[str] = []
            for index, (y, (start, end)) in enumerate(selected_rows):
                row = self._shadow.rows[y]
                stop = len(row) if end == -1 else end
                result.append(
                    "".join(
                        cell.text or " " for cell in row[start:stop] if cell.width != 0
                    ).rstrip()
                )
                if index < len(selected_rows) - 1 and not self._shadow.wraps[y]:
                    result.append("\n")
            return "".join(result), "\n"
        if self._selection_anchor is None or self._selection_end is None:
            return ""
        (ax, ay), (bx, by) = sorted(
            (self._selection_anchor, self._selection_end), key=lambda point: (point[1], point[0])
        )
        result: list[str] = []
        for y in range(ay, by + 1):
            if not 0 <= y < len(self._shadow.rows):
                continue
            start = ax if y == ay else 0
            end = bx if y == by else len(self._shadow.rows[y]) - 1
            text = "".join(
                cell.text or " "
                for cell in self._shadow.rows[y][start : end + 1]
                if cell.width != 0
            ).rstrip()
            result.append(text)
            if y < by and not self._shadow.wraps[y]:
                result.append("\n")
        return "".join(result)

    def _cell_at(self, x: int, y: int) -> Cell | None:
        if 0 <= y < len(self._shadow.rows) and 0 <= x < len(self._shadow.rows[y]):
            return self._shadow.rows[y][x]
        return None

    def hard_reset(self) -> None:
        try:
            self.terminal.hard_reset()
            self._shadow = _Shadow(-1, (), (), (), ())
            self._cursor = None
            self.refresh_frame(force=True)
        except GhosttyError as exc:
            self._fail(exc)

    async def reset_io(self) -> None:
        if self._sync_timeout_timer is not None:
            self._sync_timeout_timer.stop()
            self._sync_timeout_timer = None
        self._writer_generation += 1
        shutdown, self._queue_shutdown = self._queue_shutdown, None
        if shutdown is not None:
            shutdown.set()
        task, self._writer_task = self._writer_task, None
        queue, self._queue = self._queue, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if queue is not None:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        self.failed = False
        self._failure = None
        if self._mounted_ready:
            self._start_writer()

    def _start_writer(self) -> None:
        if self._writer_task is not None:
            return
        self._queue = asyncio.Queue(self._queue_size)
        self._queue_shutdown = asyncio.Event()
        generation = self._writer_generation
        self._writer_task = asyncio.create_task(self._writer(generation))

    async def _writer(self, generation: int) -> None:
        assert self._queue is not None
        queue = self._queue
        try:
            while generation == self._writer_generation:
                data = await queue.get()
                await self._send(data)
                queue.task_done()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail(GhosttyError(f"transport send failed: {exc}"))

    def _enqueue_nowait(self, data: bytes) -> None:
        if self.failed or self._queue is None:
            raise GhosttyError("input before mount is not accepted")
        self._queue.put_nowait(data)

    async def _enqueue(self, data: bytes) -> None:
        """Queue input for this session; discard waits interrupted by a reset.

        Cancellation retracts a put still waiting for space. Bytes already
        accepted by the queue belong to the writer and cannot be recalled.
        """
        if self.failed or self._queue is None or self._queue_shutdown is None:
            raise GhosttyError("input before mount is not accepted")
        queue = self._queue
        shutdown = self._queue_shutdown
        generation = self._writer_generation
        put_task = asyncio.create_task(queue.put(data))
        shutdown_task = asyncio.create_task(shutdown.wait())
        try:
            await asyncio.wait(
                (put_task, shutdown_task), return_when=asyncio.FIRST_COMPLETED
            )
        finally:
            put_task.cancel()
            shutdown_task.cancel()
            await asyncio.gather(put_task, shutdown_task, return_exceptions=True)
        # Cleanup yields too: check the generation only after both helpers have
        # finished, so an old handler cannot fail a replacement writer.
        if generation != self._writer_generation:
            return
        if shutdown.is_set():
            raise GhosttyError("writer queue is shut down")
        put_task.result()

    def _fail(self, error: GhosttyError) -> None:
        if self.failed:
            return
        self.failed = True
        self._failure = error
        if self._sync_timeout_timer is not None:
            self._sync_timeout_timer.stop()
            self._sync_timeout_timer = None
        self.post_message(TerminalFailed(error))
        shutdown, self._queue_shutdown = self._queue_shutdown, None
        if shutdown is not None:
            shutdown.set()
        queue, self._queue = self._queue, None
        if queue is not None:
            while not queue.empty():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
        task, self._writer_task = self._writer_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
        self.refresh()
