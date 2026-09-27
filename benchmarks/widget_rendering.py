"""Measure row rendering separately from native frame extraction."""

from __future__ import annotations

import argparse

from frame_extraction import COLS, ROWS, measure, screen

from ghostty_textual.emulator import Terminal
from ghostty_textual.widget import TerminalView


async def send(data: bytes) -> None:
    pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("sample count must be positive")
    for workload in ("plain", "styled", "alternating", "linked"):
        with Terminal(COLS, ROWS) as terminal:
            terminal.feed(screen(workload))
            view = TerminalView(send=send, terminal=terminal)
            view._apply_frame(terminal.snapshot(force=True))

            def render(view=view) -> None:
                for y in range(ROWS):
                    view.render_line(y)

            measure(workload + " render", render, args.samples)
            view._selection_anchor = (0, 0)
            view._selection_end = (COLS - 1, ROWS - 1)
            measure(workload + " selected render", render, args.samples)


if __name__ == "__main__":
    main()
