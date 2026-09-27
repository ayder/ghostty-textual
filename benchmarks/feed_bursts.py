"""Measure feed bursts through a mounted widget, including deferred extraction.

The timed region ends after one loop turn. It excludes Textual composition and
terminal output; use widget_rendering.py to measure row rendering separately.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import time

from textual.app import App, ComposeResult

from ghostty_textual.widget import TerminalView


class BenchmarkApp(App):
    def compose(self) -> ComposeResult:
        self.view = TerminalView(send=self.send)
        yield self.view

    async def send(self, data: bytes) -> None:
        pass


async def run(samples: int) -> None:
    app = BenchmarkApp()
    async with app.run_test(size=(120, 40)) as pilot:
        for chunk_size in (1, 64, 4096):
            chunks = [b"x" * chunk_size] * (4096 // chunk_size)
            values = []
            for index in range(samples + 5):
                app.view.hard_reset()
                await pilot.pause()
                start = time.perf_counter()
                for chunk in chunks:
                    app.view.feed(chunk)
                await asyncio.sleep(0)
                elapsed = (time.perf_counter() - start) * 1000
                if index >= 5:
                    values.append(elapsed)
            print(
                f"4KiB widget feed, chunks={chunk_size}: "
                f"median {statistics.median(values):.3f} ms  "
                f"p95 {sorted(values)[min(int(samples * 0.95), samples - 1)]:.3f} ms",
                flush=True,
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=10)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("sample count must be positive")
    asyncio.run(run(args.samples))
