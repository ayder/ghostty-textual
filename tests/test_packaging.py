from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path


def test_package_imports_without_loading_native_code() -> None:
    code = (
        "import sys, ghostty_textual;"
        "assert 'pyghostty._ffi' not in sys.modules;"
        "assert ghostty_textual.__version__"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_wheel_contains_typing_marker_and_license(tmp_path: Path) -> None:
    result = subprocess.run(
        ["uv", "build", "--wheel", "--no-build-isolation", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
    assert any(name.endswith("ghostty_textual/py.typed") for name in names)
    assert any("licenses/LICENSE" in name for name in names)
