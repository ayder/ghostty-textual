from __future__ import annotations

import subprocess
import sys

import pytest

RUNNER = """
import random, sys
from ghostty_textual.emulator import Terminal
rng = random.Random(int(sys.argv[1]))
with Terminal(80, 24) as terminal:
    for _ in range(100):
        terminal.feed(bytes(rng.randrange(256) for _ in range(rng.randrange(1, 64))))
        terminal.snapshot()
"""


@pytest.mark.parametrize("seed", range(4))
def test_random_bytes_never_crash_or_hang(seed: int) -> None:
    result = subprocess.run(
        [sys.executable, "-c", RUNNER, str(seed)],
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()[-2000:]
