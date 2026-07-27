from __future__ import annotations

import gc
import time
import tracemalloc

import pytest

from ghostty_textual.emulator import Terminal


@pytest.mark.performance
def test_full_frame_extraction_within_budget() -> None:
    gc.collect()
    gc.disable()
    try:
        with Terminal(120, 40, scrollback=5000) as terminal:
            terminal.feed(b"".join(b"row %d %s\r\n" % (index, b"x" * 100) for index in range(40)))
            for _ in range(10):
                terminal.snapshot(force=True)
            samples: list[float] = []
            for _ in range(100):
                start = time.perf_counter()
                terminal.snapshot(force=True)
                samples.append((time.perf_counter() - start) * 1000)
    finally:
        gc.enable()
    p95 = sorted(samples)[95]
    assert p95 < 10.0, f"p95 {p95:.2f} ms exceeds the 10 ms budget"


@pytest.mark.performance
def test_steady_state_python_heap_does_not_grow_per_frame() -> None:
    with Terminal(120, 40) as terminal:
        terminal.feed(b"x" * 100)
        terminal.snapshot(force=True)
        tracemalloc.start()
        try:
            gc.collect()
            baseline = tracemalloc.get_traced_memory()[0]
            for _ in range(2000):
                terminal.feed(b"\r")
                terminal.snapshot()
            gc.collect()
            current = tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
    assert current - baseline < 1_000_000
