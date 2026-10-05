from __future__ import annotations

import base64

import pytest

from ghostty_textual import GhosttyError
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


def _clipboard_transaction(mime: bytes, data: bytes) -> bytes:
    return (
        b"\x1b]5522;type=write:id=c1\x1b\\"
        b"\x1b]5522;type=wdata:mime="
        + base64.b64encode(mime)
        + b";"
        + base64.b64encode(data)
        + b"\x1b\\\x1b]5522;type=wdata\x1b\\"
    )


def _enable_clipboard_transaction(terminal: Terminal) -> None:
    # Enable the newer protocol only in this native acknowledgement witness.
    # Give the spool more room than the Python policy so denial reaches our callback.
    native = terminal._native
    native.check(
        native.lib.ghostty_terminal_set(
            terminal._terminal,
            native.lib.GHOSTTY_TERMINAL_OPT_CLIPBOARD_WRITE_MAX_BYTES,
            native.ffi.new("size_t*", 4096),
        ),
        "test clipboard spool",
    )


@pytest.mark.parametrize(
    ("mime", "data", "limit", "status", "notifications"),
    [
        (b"text/plain", b"hi", 10, b"DONE", (ClipboardWritten("hi"),)),
        (b"text/plain", b"hi", 1, b"EPERM", ()),
        (b"application/octet-stream", b"hi", 10, b"EPERM", ()),
    ],
)
def test_clipboard_write_replies_to_native_transaction(
    mime, data, limit, status, notifications
) -> None:
    with Terminal(20, 3, clipboard=ClipboardPolicy(True, limit)) as terminal:
        _enable_clipboard_transaction(terminal)
        effects = terminal.feed(_clipboard_transaction(mime, data))
        assert b"".join(effects.pty_writes) == (
            b"\x1b]5522;type=write:status=" + status + b":id=c1\x1b\\"
        )
        assert effects.notifications == notifications


def test_clipboard_exception_replies_with_io_error(monkeypatch: pytest.MonkeyPatch) -> None:
    with Terminal(20, 3, clipboard=ClipboardPolicy(True, 10)) as terminal:
        _enable_clipboard_transaction(terminal)

        def fail_notify(event):
            raise ValueError("clipboard notification failed")

        monkeypatch.setattr(terminal, "_notify", fail_notify)
        with pytest.raises(GhosttyError, match="clipboard notification failed"):
            terminal.feed(_clipboard_transaction(b"text/plain", b"hi"))
        assert b"".join(terminal._pty_writes) == (
            b"\x1b]5522;type=write:status=EIO:id=c1\x1b\\"
        )


@pytest.mark.parametrize("outcome", ["success", "denied", "error"])
def test_clipboard_reply_is_sized_and_not_remembered(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    with Terminal(20, 3, clipboard=ClipboardPolicy(True, 10)) as terminal:
        ffi, lib = terminal._native.ffi, terminal._native.lib
        callback = next(
            callback
            for callback in terminal._keepalive
            if "GhosttyClipboardWrite *" in ffi.typeof(callback).cname
        )
        assert ffi.typeof(callback).result.kind == "void"
        replies = []

        @ffi.callback("void(const GhosttyClipboardWrite*, const GhosttyClipboardWriteReply*)")
        def capture_reply(request, reply):
            replies.append((reply.size, reply.result, bool(reply.remember)))

        mime = ffi.new("char[]", b"text/plain" if outcome != "denied" else b"image/png")
        data = ffi.new("char[]", b"hi")
        contents = ffi.new("GhosttyClipboardContent[]", 1)
        contents[0].mime.ptr, contents[0].mime.len = mime, len(ffi.string(mime))
        contents[0].data.ptr, contents[0].data.len = data, 2
        request = ffi.new("GhosttyClipboardWrite*")
        request.size = ffi.sizeof("GhosttyClipboardWrite")
        request.contents, request.contents_len = contents, 1
        request.can_remember = True
        request.reply = capture_reply
        if outcome == "error":

            def fail_notify(event):
                raise ValueError("callback failed")

            monkeypatch.setattr(terminal, "_notify", fail_notify)
        assert callback(terminal._terminal, ffi.NULL, request) is None
        expected = {
            "success": lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_SUCCESS,
            "denied": lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_DENIED,
            "error": lib.GHOSTTY_CLIPBOARD_WRITE_RESULT_IO_ERROR,
        }[outcome]
        assert replies == [(ffi.sizeof("GhosttyClipboardWriteReply"), expected, False)]


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
