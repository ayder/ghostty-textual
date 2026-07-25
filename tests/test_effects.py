"""Terminal-generated PTY writes and terminal identity.

Spec v2 §3.2. pyte answered no queries at all -- ADR-0001 lists this as a
knowingly accepted gap. These tests pin what libghostty answers automatically,
what it stays silent about, and what our own callback contributes.
"""

from __future__ import annotations

import pytest

from tests.conftest import Harness


class TestAutomaticReplies:
    """Answered with only WRITE_PTY registered -- no other configuration."""

    def test_dsr_cursor_position(self, terminal: Harness) -> None:
        assert terminal.reply_to(b"\x1b[6n") == [b"\x1b[1;1R"]

    def test_dsr_reports_actual_cursor(self, terminal: Harness) -> None:
        terminal.feed(b"hello")
        assert terminal.reply_to(b"\x1b[6n") == [b"\x1b[1;6R"]

    def test_primary_device_attributes(self, terminal: Harness) -> None:
        assert terminal.reply_to(b"\x1b[c") == [b"\x1b[?62;22c"]

    def test_xtversion_identifies_as_libghostty(self, terminal: Harness) -> None:
        """Not configurable -- which is why spec v2 dropped the TerminalProfile idea."""
        assert terminal.reply_to(b"\x1b[>0q") == [b"\x1bP>|libghostty\x1b\\"]


class TestDeliberateSilence:
    """Unanswered by design in v1. Each needs a callback we do not register."""

    @pytest.mark.parametrize(
        ("name", "sequence"),
        [
            ("ENQ", b"\x05"),
            ("OSC 11 background query", b"\x1b]11;?\x07"),
        ],
    )
    def test_no_reply(self, terminal: Harness, name: str, sequence: bytes) -> None:
        assert terminal.reply_to(sequence) == [], f"{name} unexpectedly answered"


class TestOrdering:
    def test_writes_are_delivered_in_order(self, terminal: Harness) -> None:
        """One feed producing several replies must preserve their order.

        Spec v2 §6.2 routes these through the same FIFO as keystrokes; the
        ordering guarantee starts here.
        """
        terminal.drain()
        terminal.feed(b"\x1b[6n\x1b[c\x1b[6n")
        assert terminal.drain() == [b"\x1b[1;1R", b"\x1b[?62;22c", b"\x1b[1;1R"]

    def test_callback_survives_garbage_collection(self, terminal: Harness) -> None:
        """The harness holds the CFFI callback; C holds only a raw pointer."""
        import gc

        gc.collect()
        assert terminal.reply_to(b"\x1b[6n") == [b"\x1b[1;1R"]
