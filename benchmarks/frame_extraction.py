"""Extraction and feed benchmarks; run from the repository's Python environment."""

from __future__ import annotations

import argparse
import statistics
import time
from collections.abc import Callable

from ghostty_textual.emulator import Terminal

COLS, ROWS = 120, 40


def screen(workload: str) -> bytes:
    line = {
        "plain": b"x" * COLS,
        "styled": b"\x1b[1;38;2;120;80;160m" + b"x" * COLS + b"\x1b[0m",
        "alternating": b"\x1b[31mx\x1b[32mx" * (COLS // 2) + b"\x1b[0m",
        "linked": b"\x1b]8;;https://example.com\x1b\\" + b"x" * COLS + b"\x1b]8;;\x1b\\",
    }[workload]
    return b"\x1b[?25l" + b"".join(b"\x1b[%d;1H" % (y + 1) + line for y in range(ROWS))


def measure(
    name: str,
    operation: Callable[[], object],
    count: int,
    *,
    prepare: Callable[[], object] | None = None,
) -> None:
    values: list[float] = []
    for index in range(count + 5):
        if prepare is not None:
            prepare()
        start = time.perf_counter()
        operation()
        elapsed = (time.perf_counter() - start) * 1000
        if index >= 5:
            values.append(elapsed)
    ordered = sorted(values)
    print(
        f"{name}: median {statistics.median(values):.3f} ms  "
        f"p95 {ordered[min(int(count * 0.95), count - 1)]:.3f} ms",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--fragment-samples", type=int, default=10)
    args = parser.parse_args()
    if min(args.samples, args.fragment_samples) < 1:
        parser.error("sample counts must be positive")

    for workload in ("plain", "styled", "alternating", "linked"):
        with Terminal(COLS, ROWS) as terminal:
            terminal.feed(screen(workload))
            measure(
                workload + " full extraction", lambda: terminal.snapshot(force=True), args.samples
            )
    with Terminal(COLS, ROWS) as terminal:
        terminal.feed(screen("plain"))
        terminal.snapshot()
        measure("idle snapshot", terminal.snapshot, args.samples)
        measure(
            "single dirty row",
            terminal.snapshot,
            args.samples,
            prepare=lambda: terminal.feed(b"\x1b[1;1Hx"),
        )
        measure(
            "cursor-only snapshot",
            terminal.snapshot,
            args.samples,
            prepare=lambda: terminal.feed(
                b"\x1b[1;1H" if terminal._last_cursor.x else b"\x1b[1;2H"
            ),
        )
        measure(
            "suppressed sync snapshot",
            terminal.snapshot,
            args.samples,
            prepare=lambda: terminal.feed(b"\x1b[?2026l\x1b[?2026hx"),
        )

    for chunk_size, every_chunk in ((1, True), (64, True), (4096, True), (1, False)):
        with Terminal(COLS, ROWS) as terminal:
            chunks = [b"x" * chunk_size] * (4096 // chunk_size)

            def run(chunks=chunks, every_chunk=every_chunk) -> None:
                for chunk in chunks:
                    terminal.feed(chunk)
                    if every_chunk:
                        terminal.snapshot()
                if not every_chunk:
                    terminal.snapshot()

            def reset() -> None:
                terminal.hard_reset()
                terminal.snapshot()

            label = "each chunk" if every_chunk else "once"
            measure(
                f"4KiB feed, chunks={chunk_size}, snapshot {label}",
                run,
                args.fragment_samples,
                prepare=reset,
            )


if __name__ == "__main__":
    main()
