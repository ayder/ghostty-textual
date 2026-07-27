"""Headless libghostty terminal: bytes in, effects and immutable frames out."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from ghostty_textual._native import GhosttyError, load
from ghostty_textual._render import CursorState, RenderState, RowPatch, ViewportState
from ghostty_textual.cells import CellStyle, InternerFull, LinkInterner, Rgb, StyleInterner
from ghostty_textual.keys import KeyEvent, MouseEvent
from ghostty_textual.theme import DEFAULT_THEME, TerminalTheme


@dataclass(frozen=True, slots=True)
class ClipboardPolicy:
    allow_write: bool = False
    max_bytes: int = 1_000_000


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    max_interned_styles: int = 4096
    max_interned_links: int = 1024
    max_link_uri_bytes: int = 2048
    kitty_image_storage_bytes: int = 0
    apc_max_bytes: int = 8192
    apc_max_bytes_kitty: int = 8192
    notification_rate_per_sec: int = 60


DEFAULT_CLIPBOARD_POLICY = ClipboardPolicy()
DEFAULT_RESOURCE_LIMITS = ResourceLimits()


@dataclass(frozen=True, slots=True)
class TitleChanged:
    title: str


@dataclass(frozen=True, slots=True)
class BellRang:
    pass


@dataclass(frozen=True, slots=True)
class ClipboardWritten:
    text: str


@dataclass(frozen=True, slots=True)
class ProcessingError:
    pass


TerminalNotification = TitleChanged | BellRang | ClipboardWritten | ProcessingError


@dataclass(frozen=True, slots=True)
class TerminalEffects:
    pty_writes: tuple[bytes, ...] = ()
    notifications: tuple[TerminalNotification, ...] = ()


@dataclass(frozen=True, slots=True)
class Frame:
    generation: int
    cols: int
    rows: int
    full_redraw: bool
    row_patches: tuple[RowPatch, ...]
    styles: tuple[CellStyle, ...]
    links: tuple[str, ...]
    cursor: CursorState
    viewport: ViewportState
    frame_pending: bool


@dataclass(frozen=True, slots=True)
class TerminalModes:
    bracketed_paste: bool
    focus_events: bool
    app_cursor_keys: bool
    alt_screen: bool
    mouse_tracking: int
    mouse_encoding: int
    sync_output: bool


@dataclass(frozen=True, slots=True)
class ScrollTop:
    pass


@dataclass(frozen=True, slots=True)
class ScrollBottom:
    pass


@dataclass(frozen=True, slots=True)
class ScrollDelta:
    value: int


@dataclass(frozen=True, slots=True)
class ScrollToRow:
    value: int


ScrollRequest = ScrollTop | ScrollBottom | ScrollDelta | ScrollToRow


class Terminal:
    """A thread-confined terminal emulator that owns no process or transport."""

    def __init__(
        self,
        cols: int,
        rows: int,
        *,
        scrollback: int = 5000,
        theme: TerminalTheme | None = None,
        clipboard: ClipboardPolicy = DEFAULT_CLIPBOARD_POLICY,
        limits: ResourceLimits = DEFAULT_RESOURCE_LIMITS,
    ) -> None:
        if cols <= 0 or rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        self._native = load()
        self._owner_thread = threading.get_ident()
        self._cols, self._rows = cols, rows
        self._scrollback = scrollback
        self._theme = theme or DEFAULT_THEME
        self._clipboard = clipboard
        self._limits = limits
        self._terminal: Any = None
        self._render: RenderState | None = None
        self._key_encoder: Any = None
        self._mouse_encoder: Any = None
        self._keepalive: list[Any] = []
        self._pty_writes: list[bytes] = []
        self._notifications: list[TerminalNotification] = []
        self._callback_error: BaseException | None = None
        self._styles = StyleInterner()
        self._links = LinkInterner(
            limit=limits.max_interned_links, max_uri_bytes=limits.max_link_uri_bytes
        )
        self._notification_times: deque[float] = deque()
        self._force_redraw = True
        self._sync_started: float | None = None
        self._last_cursor: CursorState | None = None
        self._last_color_signature: tuple[tuple[int, int, int], ...] | None = None
        self._poisoned: GhosttyError | None = None
        self.closed = False
        self._create_native()

    @property
    def cols(self) -> int:
        self._assert_usable()
        return self._cols

    @property
    def rows(self) -> int:
        self._assert_usable()
        return self._rows

    @property
    def theme(self) -> TerminalTheme:
        self._assert_usable()
        return self._theme

    @property
    def effective_style_limit(self) -> int:
        self._assert_usable()
        return self._styles.limit

    @property
    def kitty_image_storage_bytes(self) -> int:
        self._assert_usable()
        return self._limits.kitty_image_storage_bytes

    def _create_native(self) -> None:
        ffi, lib = self._native.ffi, self._native.lib
        handle = ffi.new("GhosttyTerminal*")
        options = ffi.new(
            "GhosttyTerminalOptions*",
            dict(cols=self._cols, rows=self._rows, max_scrollback=self._scrollback),
        )
        self._native.check(lib.ghostty_terminal_new(ffi.NULL, handle, options[0]), "terminal_new")
        self._terminal = handle[0]
        try:
            self._register_callbacks()
            self._apply_limits()
            self._apply_theme()
            self._render = RenderState(self._native)
            key = ffi.new("GhosttyKeyEncoder*")
            mouse = ffi.new("GhosttyMouseEncoder*")
            self._native.check(lib.ghostty_key_encoder_new(ffi.NULL, key), "key_encoder_new")
            self._key_encoder = key[0]
            self._native.check(lib.ghostty_mouse_encoder_new(ffi.NULL, mouse), "mouse_encoder_new")
            self._mouse_encoder = mouse[0]
            self._apply_style_limit()
        except Exception:
            self._free_native()
            raise

    def _register_callbacks(self) -> None:
        ffi, lib = self._native.ffi, self._native.lib

        @ffi.callback("void(GhosttyTerminal, void*, const uint8_t*, size_t)")
        def write_pty(term, userdata, data, length):  # noqa: ANN001, ARG001
            try:
                self._pty_writes.append(bytes(ffi.buffer(data, length)))
            except BaseException as exc:
                self._callback_error = exc

        @ffi.callback("bool(GhosttyTerminal, void*, GhosttySizeReportSize*)")
        def size(term, userdata, out):  # noqa: ANN001, ARG001
            try:
                out.rows, out.columns = self._rows, self._cols
                out.cell_width = out.cell_height = 0
                return True
            except BaseException as exc:
                self._callback_error = exc
                return False

        @ffi.callback("void(GhosttyTerminal, void*)")
        def title_changed(term, userdata):  # noqa: ANN001, ARG001
            try:
                self._notify(TitleChanged(self.title or ""))
            except BaseException as exc:
                self._callback_error = exc

        @ffi.callback("void(GhosttyTerminal, void*)")
        def bell(term, userdata):  # noqa: ANN001, ARG001
            try:
                self._notify(BellRang())
            except BaseException as exc:
                self._callback_error = exc

        callbacks = (
            (lib.GHOSTTY_TERMINAL_OPT_WRITE_PTY, write_pty),
            (lib.GHOSTTY_TERMINAL_OPT_SIZE, size),
            (lib.GHOSTTY_TERMINAL_OPT_TITLE_CHANGED, title_changed),
            (lib.GHOSTTY_TERMINAL_OPT_BELL, bell),
        )
        self._keepalive.extend(callback for _, callback in callbacks)
        for option, callback in callbacks:
            self._native.check(
                lib.ghostty_terminal_set(self._terminal, option, ffi.cast("void*", callback)),
                f"terminal_set {option}",
            )
        if self._clipboard.allow_write:
            self._register_clipboard_callback()

    def _register_clipboard_callback(self) -> None:
        ffi, lib = self._native.ffi, self._native.lib

        @ffi.callback(
            "GhosttyClipboardWriteResult(GhosttyTerminal, void*, const GhosttyClipboardWrite*)"
        )
        def clipboard_write(term, userdata, write):  # noqa: ANN001, ARG001
            try:
                for index in range(int(write.contents_len)):
                    item = write.contents[index]
                    mime = bytes(ffi.buffer(item.mime.ptr, item.mime.len)).decode(
                        "ascii", "replace"
                    )
                    data = bytes(ffi.buffer(item.data.ptr, item.data.len))
                    if mime.startswith("text/") and len(data) <= self._clipboard.max_bytes:
                        self._notify(ClipboardWritten(data.decode("utf-8", "replace")))
                        return lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_SUCCESS
                return lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_DENIED
            except BaseException as exc:
                self._callback_error = exc
                return lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_IO_ERROR

        self._keepalive.append(clipboard_write)
        self._native.check(
            lib.ghostty_terminal_set(
                self._terminal,
                lib.GHOSTTY_TERMINAL_OPT_CLIPBOARD_WRITE,
                ffi.cast("void*", clipboard_write),
            ),
            "terminal_set CLIPBOARD_WRITE",
        )

    def _apply_limits(self) -> None:
        ffi, lib = self._native.ffi, self._native.lib
        values = (
            (
                lib.GHOSTTY_TERMINAL_OPT_KITTY_IMAGE_STORAGE_LIMIT,
                self._limits.kitty_image_storage_bytes,
            ),
            (lib.GHOSTTY_TERMINAL_OPT_APC_MAX_BYTES, self._limits.apc_max_bytes),
            (
                lib.GHOSTTY_TERMINAL_OPT_APC_MAX_BYTES_KITTY,
                self._limits.apc_max_bytes_kitty,
            ),
        )
        for option, value in values:
            pointer = ffi.new("size_t*", value)
            self._native.check(
                lib.ghostty_terminal_set(self._terminal, option, pointer),
                f"terminal_set {option}",
            )

    def _apply_theme(self) -> None:
        ffi, lib = self._native.ffi, self._native.lib
        for option, rgb in (
            (lib.GHOSTTY_TERMINAL_OPT_COLOR_FOREGROUND, self._theme.foreground),
            (lib.GHOSTTY_TERMINAL_OPT_COLOR_BACKGROUND, self._theme.background),
            (lib.GHOSTTY_TERMINAL_OPT_COLOR_CURSOR, self._theme.cursor),
        ):
            color = ffi.new("GhosttyColorRgb*", dict(zip(("r", "g", "b"), rgb, strict=True)))
            self._native.check(
                lib.ghostty_terminal_set(self._terminal, option, color),
                f"terminal_set {option}",
            )
        palette = ffi.new("GhosttyColorRgb[256]")
        for index, rgb in enumerate(self._theme.palette):
            palette[index].r, palette[index].g, palette[index].b = rgb
        self._native.check(
            lib.ghostty_terminal_set(
                self._terminal, lib.GHOSTTY_TERMINAL_OPT_COLOR_PALETTE, palette
            ),
            "terminal_set COLOR_PALETTE",
        )

    def feed(self, data: bytes) -> TerminalEffects:
        self._assert_usable()
        was_syncing = self._mode(2026)
        self._pty_writes.clear()
        self._notifications.clear()
        self._callback_error = None
        self._native.lib.ghostty_terminal_vt_write(self._terminal, data, len(data))
        if self._callback_error is not None:
            raise GhosttyError(f"terminal callback failed: {self._callback_error}") from (
                self._callback_error
            )
        if self._processing_error:
            self._notify(ProcessingError())
        is_syncing = self._mode(2026)
        if is_syncing and not was_syncing:
            self._sync_started = time.monotonic()
        elif not is_syncing:
            self._sync_started = None
        return TerminalEffects(tuple(self._pty_writes), tuple(self._notifications))

    @property
    def _processing_error(self) -> bool:
        ffi, lib = self._native.ffi, self._native.lib
        out = ffi.new("bool*")
        rc = lib.ghostty_terminal_get(
            self._terminal, lib.GHOSTTY_TERMINAL_DATA_VT_PROCESSING_ERROR, out
        )
        return not rc and bool(out[0])

    def snapshot(self, *, force: bool = False) -> Frame | None:
        """Copy changed rows; recover a dropped frame with ``force=True``."""
        self._assert_usable()
        assert self._render is not None
        self._render.update(self._terminal)
        cursor = self._render.read_cursor()
        color_signature = self._render.color_signature()
        if (
            not force
            and self.modes.sync_output
            and self._sync_started is not None
            and time.monotonic() - self._sync_started < 0.150
        ):
            return None
        force = force or self._force_redraw
        if self._last_color_signature is not None and color_signature != self._last_color_signature:
            force = True
        cursor_changed = self._last_cursor is not None and cursor != self._last_cursor
        dirty = self._render.is_dirty()
        if not force and not dirty and not cursor_changed:
            return None
        if force or dirty:
            for _attempt in range(2):
                try:
                    patches = self._render.read_rows(self._styles, self._links, force=force)
                    break
                except InternerFull:
                    self._styles.rollover()
                    self._links.rollover()
                    self._render.update(self._terminal)
                    cursor = self._render.read_cursor()
                    color_signature = self._render.color_signature()
                    force = True
            else:
                raise GhosttyError("style interning failed twice in one snapshot")
        else:
            patches = ()
        frame = Frame(
            generation=self._styles.generation,
            cols=self._cols,
            rows=self._rows,
            full_redraw=force,
            row_patches=patches,
            styles=self._styles.table(),
            links=self._links.table(),
            cursor=cursor,
            viewport=self._read_viewport(),
            frame_pending=self.modes.sync_output,
        )
        self._render.clear_global_dirty()
        self._force_redraw = False
        self._last_cursor = cursor
        self._last_color_signature = color_signature
        return frame

    def resize(self, cols: int, rows: int) -> None:
        self._assert_usable()
        if cols <= 0 or rows <= 0:
            raise ValueError("terminal dimensions must be positive")
        self._native.check(
            self._native.lib.ghostty_terminal_resize(self._terminal, cols, rows, 0, 0),
            "terminal_resize",
        )
        self._cols, self._rows = cols, rows
        self._apply_style_limit()
        self._force_redraw = True

    def _apply_style_limit(self) -> None:
        self._styles.set_limit(max(self._limits.max_interned_styles, self._cols * self._rows))

    def scroll_viewport(self, request: ScrollRequest) -> None:
        self._assert_usable()
        lib = self._native.lib
        if isinstance(request, ScrollTop):
            tag, value = lib.GHOSTTY_SCROLL_VIEWPORT_TOP, 0
        elif isinstance(request, ScrollBottom):
            tag, value = lib.GHOSTTY_SCROLL_VIEWPORT_BOTTOM, 0
        elif isinstance(request, ScrollDelta):
            tag, value = lib.GHOSTTY_SCROLL_VIEWPORT_DELTA, request.value
        else:
            tag, value = lib.GHOSTTY_SCROLL_VIEWPORT_ROW, request.value
        self._native.scroll_viewport(self._terminal, tag, value)
        self._force_redraw = True

    def scroll_to_top(self) -> None:
        self.scroll_viewport(ScrollTop())

    def scroll_to_bottom(self) -> None:
        self.scroll_viewport(ScrollBottom())

    @property
    def viewport(self) -> ViewportState:
        self._assert_usable()
        return self._read_viewport()

    def _read_viewport(self) -> ViewportState:
        ffi, lib = self._native.ffi, self._native.lib
        bar = ffi.new("GhosttyTerminalScrollbar*")
        self._native.check(
            lib.ghostty_terminal_get(self._terminal, lib.GHOSTTY_TERMINAL_DATA_SCROLLBAR, bar),
            "terminal_get SCROLLBAR",
        )
        active = self._get_terminal_bool(lib.GHOSTTY_TERMINAL_DATA_VIEWPORT_ACTIVE)
        return ViewportState(
            at_bottom=active,
            offset=int(bar.offset),
            scrollback_rows=self._get_terminal_u32(lib.GHOSTTY_TERMINAL_DATA_SCROLLBACK_ROWS),
            total_rows=self._get_terminal_u32(lib.GHOSTTY_TERMINAL_DATA_TOTAL_ROWS),
        )

    @property
    def title(self) -> str | None:
        self._assert_usable()
        ffi, lib = self._native.ffi, self._native.lib
        value = ffi.new("GhosttyString*")
        rc = lib.ghostty_terminal_get(self._terminal, lib.GHOSTTY_TERMINAL_DATA_TITLE, value)
        if rc or value.ptr == ffi.NULL:
            return None
        return bytes(ffi.buffer(value.ptr, value.len)).decode("utf-8", "replace")

    @property
    def colour_override_foreground(self) -> Rgb | None:
        self._assert_usable()
        ffi, lib = self._native.ffi, self._native.lib
        value = ffi.new("uint32_t*")
        rc = lib.ghostty_terminal_get(
            self._terminal, lib.GHOSTTY_TERMINAL_DATA_COLOR_FOREGROUND, value
        )
        if rc:
            return None
        packed = int(value[0])
        return (packed & 255, (packed >> 8) & 255, (packed >> 16) & 255)

    @property
    def modes(self) -> TerminalModes:
        self._assert_usable()
        return TerminalModes(
            bracketed_paste=self._mode(2004),
            focus_events=self._mode(1004),
            app_cursor_keys=self._mode(1),
            alt_screen=self._get_terminal_u32(self._native.lib.GHOSTTY_TERMINAL_DATA_ACTIVE_SCREEN)
            == self._native.lib.GHOSTTY_TERMINAL_SCREEN_ALTERNATE,
            mouse_tracking=self._get_terminal_u32(
                self._native.lib.GHOSTTY_TERMINAL_DATA_MOUSE_TRACKING
            ),
            mouse_encoding=self._mouse_encoding(),
            sync_output=self._mode(2026),
        )

    def _mouse_encoding(self) -> int:
        lib = self._native.lib
        for mode, encoding in (
            (1016, lib.GHOSTTY_MOUSE_FORMAT_SGR_PIXELS),
            (1006, lib.GHOSTTY_MOUSE_FORMAT_SGR),
            (1015, lib.GHOSTTY_MOUSE_FORMAT_URXVT),
            (1005, lib.GHOSTTY_MOUSE_FORMAT_UTF8),
        ):
            if self._mode(mode):
                return int(encoding)
        return int(lib.GHOSTTY_MOUSE_FORMAT_X10)

    def _mode(self, mode: int) -> bool:
        out = self._native.ffi.new("bool*")
        rc = self._native.lib.ghostty_terminal_mode_get(self._terminal, mode, out)
        return not rc and bool(out[0])

    def encode_key(self, event: KeyEvent) -> bytes | None:
        self._assert_usable()
        ffi, lib = self._native.ffi, self._native.lib
        key = _ghostty_key(lib, event.key)
        if key is None and event.text is not None:
            key = lib.GHOSTTY_KEY_UNIDENTIFIED
        elif key is None:
            return None
        holder = ffi.new("GhosttyKeyEvent*")
        self._native.check(lib.ghostty_key_event_new(ffi.NULL, holder), "key_event_new")
        native_event = holder[0]
        try:
            lib.ghostty_key_event_set_action(native_event, lib.GHOSTTY_KEY_ACTION_PRESS)
            lib.ghostty_key_event_set_key(native_event, key)
            mods = event.shift | (event.ctrl << 1) | (event.alt << 2) | (event.meta << 3)
            lib.ghostty_key_event_set_mods(native_event, mods)
            if event.text:
                encoded = event.text.encode()
                lib.ghostty_key_event_set_utf8(native_event, encoded, len(encoded))
            lib.ghostty_key_encoder_setopt_from_terminal(self._key_encoder, self._terminal)
            return self._encode_buffer(
                lib.ghostty_key_encoder_encode, self._key_encoder, native_event
            )
        finally:
            lib.ghostty_key_event_free(native_event)

    def encode_focus(self, focused: bool) -> bytes | None:
        self._assert_usable()
        if not self.modes.focus_events:
            return None
        event = (
            self._native.lib.GHOSTTY_FOCUS_GAINED
            if focused
            else self._native.lib.GHOSTTY_FOCUS_LOST
        )
        return self._encode_buffer(self._native.lib.ghostty_focus_encode, event)

    def encode_paste(self, text: str) -> bytes | None:
        self._assert_usable()
        data = text.encode()
        bracketed = self.modes.bracketed_paste
        if not bracketed and not self._native.lib.ghostty_paste_is_safe(data, len(data)):
            return None
        return self._encode_buffer(
            self._native.lib.ghostty_paste_encode, data, len(data), bracketed, size=len(data) + 64
        )

    def encode_mouse(self, event: MouseEvent) -> bytes | None:
        self._assert_usable()
        ffi, lib = self._native.ffi, self._native.lib
        holder = ffi.new("GhosttyMouseEvent*")
        self._native.check(lib.ghostty_mouse_event_new(ffi.NULL, holder), "mouse_event_new")
        native_event = holder[0]
        try:
            actions = {
                "press": lib.GHOSTTY_MOUSE_ACTION_PRESS,
                "release": lib.GHOSTTY_MOUSE_ACTION_RELEASE,
                "motion": lib.GHOSTTY_MOUSE_ACTION_MOTION,
            }
            lib.ghostty_mouse_event_set_action(native_event, actions[event.action])
            if event.button is None:
                lib.ghostty_mouse_event_clear_button(native_event)
            else:
                button = getattr(lib, f"GHOSTTY_MOUSE_BUTTON_{_mouse_button_name(event.button)}")
                lib.ghostty_mouse_event_set_button(native_event, button)
            mods = event.shift | (event.ctrl << 1) | (event.alt << 2) | (event.meta << 3)
            lib.ghostty_mouse_event_set_mods(native_event, mods)
            position = ffi.new("GhosttyMousePosition*", {"x": event.x, "y": event.y})
            lib.ghostty_mouse_event_set_position(native_event, position[0])
            lib.ghostty_mouse_encoder_setopt_from_terminal(self._mouse_encoder, self._terminal)
            return self._encode_buffer(
                lib.ghostty_mouse_encoder_encode, self._mouse_encoder, native_event
            )
        finally:
            lib.ghostty_mouse_event_free(native_event)

    def _encode_buffer(self, function: Any, *args: Any, size: int = 256) -> bytes | None:
        ffi = self._native.ffi
        buf = ffi.new("char[]", size)
        written = ffi.new("size_t*")
        self._native.check(function(*args, buf, size, written), function.__name__)
        if written[0] == 0:
            return None
        return bytes(ffi.buffer(buf, int(written[0])))

    def set_theme(self, theme: TerminalTheme) -> None:
        self._assert_usable()
        self._theme = theme
        self._apply_theme()
        self._force_redraw = True

    def hard_reset(self) -> None:
        self._assert_thread()
        if self.closed:
            raise RuntimeError("Terminal is closed")
        self._free_native()
        self._styles = StyleInterner()
        self._links = LinkInterner(
            limit=self._limits.max_interned_links,
            max_uri_bytes=self._limits.max_link_uri_bytes,
        )
        self._keepalive = []
        self._pty_writes = []
        self._notifications = []
        self._notification_times.clear()
        self._sync_started = None
        self._last_cursor = None
        self._last_color_signature = None
        self._poisoned = None
        try:
            self._create_native()
        except GhosttyError as exc:
            self._poisoned = exc
            raise
        except Exception as exc:
            error = GhosttyError(f"hard reset failed while recreating terminal: {exc}")
            self._poisoned = error
            raise error from exc
        self._force_redraw = True

    def resolve_link(self, link_id: int) -> str | None:
        self._assert_usable()
        try:
            return self._links.resolve(link_id)
        except KeyError:
            return None

    def close(self) -> None:
        if self.closed:
            return
        self._assert_thread()
        self._free_native()
        self.closed = True

    def _free_native(self) -> None:
        lib, ffi = self._native.lib, self._native.ffi
        if self._mouse_encoder not in (None, ffi.NULL):
            lib.ghostty_mouse_encoder_free(self._mouse_encoder)
            self._mouse_encoder = None
        if self._key_encoder not in (None, ffi.NULL):
            lib.ghostty_key_encoder_free(self._key_encoder)
            self._key_encoder = None
        if self._render is not None:
            self._render.close()
            self._render = None
        if self._terminal not in (None, ffi.NULL):
            lib.ghostty_terminal_free(self._terminal)
            self._terminal = None
        self._keepalive.clear()

    def _notify(self, notification: TerminalNotification) -> None:
        now = time.monotonic()
        window = self._notification_times
        while window and window[0] <= now - 1:
            window.popleft()
        if len(window) < self._limits.notification_rate_per_sec:
            window.append(now)
            self._notifications.append(notification)

    def _get_terminal_u32(self, key: int) -> int:
        return self._native.get_u32(
            self._native.lib.ghostty_terminal_get,
            self._terminal,
            key,
            "terminal_get",
        )

    def _get_terminal_bool(self, key: int) -> bool:
        return self._native.get_bool(
            self._native.lib.ghostty_terminal_get,
            self._terminal,
            key,
            "terminal_get",
        )

    def _assert_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("Terminal may only be used from its owning thread")

    def _assert_usable(self) -> None:
        self._assert_thread()
        if self.closed:
            raise RuntimeError("Terminal is closed")
        if self._poisoned is not None:
            raise self._poisoned
        if (
            self._terminal is None
            or self._render is None
            or self._key_encoder is None
            or self._mouse_encoder is None
        ):
            error = GhosttyError("Terminal native state is unavailable")
            self._poisoned = error
            raise error

    def __enter__(self) -> Terminal:
        self._assert_usable()
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def _ghostty_key(lib: Any, key: str) -> int | None:
    names = {
        "up": "ARROW_UP",
        "down": "ARROW_DOWN",
        "left": "ARROW_LEFT",
        "right": "ARROW_RIGHT",
        "pageup": "PAGE_UP",
        "pagedown": "PAGE_DOWN",
        "enter": "ENTER",
        "return": "ENTER",
        "escape": "ESCAPE",
        "esc": "ESCAPE",
        "backspace": "BACKSPACE",
        "tab": "TAB",
        "space": "SPACE",
        "delete": "DELETE",
        "insert": "INSERT",
        "home": "HOME",
        "end": "END",
        "minus": "MINUS",
        "hyphen": "MINUS",
        "-": "MINUS",
        "period": "PERIOD",
        "full_stop": "PERIOD",
        ".": "PERIOD",
        "comma": "COMMA",
        ",": "COMMA",
        "quote": "QUOTE",
        "'": "QUOTE",
        "semicolon": "SEMICOLON",
        ";": "SEMICOLON",
        "slash": "SLASH",
        "/": "SLASH",
        "backslash": "BACKSLASH",
        "\\": "BACKSLASH",
        "left_square_bracket": "BRACKET_LEFT",
        "[": "BRACKET_LEFT",
        "right_square_bracket": "BRACKET_RIGHT",
        "]": "BRACKET_RIGHT",
        "equals_sign": "EQUAL",
        "equal": "EQUAL",
        "=": "EQUAL",
        "grave_accent": "BACKQUOTE",
        "`": "BACKQUOTE",
    }
    suffix = names.get(key.lower())
    if suffix is None and len(key) == 1 and key.isalpha():
        suffix = key.upper()
    elif suffix is None and len(key) == 1 and key.isdigit():
        suffix = f"DIGIT_{key}"
    elif suffix is None and key.lower().startswith("numpad_"):
        suffix = key.upper()
    elif suffix is None and key.lower().startswith("f") and key[1:].isdigit():
        suffix = key.upper()
    if suffix is None:
        return None
    return getattr(lib, f"GHOSTTY_KEY_{suffix}", None)


def _mouse_button_name(button: int) -> str:
    names = {
        1: "LEFT",
        2: "MIDDLE",
        3: "RIGHT",
        4: "FOUR",
        5: "FIVE",
        6: "SIX",
        7: "SEVEN",
        8: "EIGHT",
        9: "NINE",
        10: "TEN",
        11: "ELEVEN",
    }
    return names.get(button, "UNKNOWN")
