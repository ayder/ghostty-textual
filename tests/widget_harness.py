from __future__ import annotations

import asyncio

from textual.app import App, ComposeResult
from textual.message import Message

from ghostty_textual.emulator import Terminal
from ghostty_textual.widget import TerminalView


class Harness(App):
    def __init__(
        self,
        *,
        terminal: Terminal | None = None,
        close_terminal: bool = False,
        reserved_keys: frozenset[str] = frozenset(),
        queue_size: int = 256,
        stalled_send: bool = False,
        resize_transport=None,
    ) -> None:
        super().__init__()
        self.sent: list[bytes] = []
        self.captured: list[Message] = []
        self._gate = asyncio.Event()
        if not stalled_send:
            self._gate.set()
        self._options = dict(
            terminal=terminal,
            close_terminal=close_terminal,
            reserved_keys=reserved_keys,
            queue_size=queue_size,
            resize_transport=resize_transport,
        )

    def compose(self) -> ComposeResult:
        self.view = TerminalView(send=self._send, **self._options)
        yield self.view

    async def _send(self, data: bytes) -> None:
        await self._gate.wait()
        self.sent.append(data)

    def release_send(self) -> None:
        self._gate.set()

    def messages_of(self, kind: type[Message]) -> list[Message]:
        return [message for message in self.captured if isinstance(message, kind)]

    async def on_message(self, message: Message) -> None:
        self.captured.append(message)

    def on_terminal_view_resized(self, message: Message) -> None:
        self.captured.append(message)

    def on_terminal_view_terminal_failed(self, message: Message) -> None:
        self.captured.append(message)

    def on_terminal_view_paste_rejected(self, message: Message) -> None:
        self.captured.append(message)
