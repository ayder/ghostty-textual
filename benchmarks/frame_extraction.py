"""Measure the spec v2 full-frame and dirty-row extraction budgets."""

from __future__ import annotations

import statistics
import time

from ghostty_textual.emulator import Terminal


def samples() -> tuple[list[float], list[float]]:
    with Terminal(120, 40, scrollback=5000) as terminal:
        terminal.feed(b"".join(b"row %d %s\r\n" % (index, b"x" * 100) for index in range(40)))
        full: list[float] = []
        single: list[float] = []
        for _ in range(100):
            start = time.perf_counter()
            terminal.snapshot(force=True)
            full.append((time.perf_counter() - start) * 1000)
        for index in range(100):
            terminal.feed(b"\x1b[1;1H%d" % (index % 10))
            start = time.perf_counter()
            terminal.snapshot()
            single.append((time.perf_counter() - start) * 1000)
    return full, single


def main() -> None:
    full, single = samples()
    for name, values in (("full 120x40", full), ("single dirty row", single)):
        ordered = sorted(values)
        p95 = ordered[int(len(ordered) * 0.95)]
        print(f"{name}: median {statistics.median(values):.3f} ms  p95 {p95:.3f} ms")


if __name__ == "__main__":
    main()
