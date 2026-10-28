from __future__ import annotations

import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

from packaging.requirements import Requirement


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


def test_distributions_have_matching_metadata_and_public_files(tmp_path: Path) -> None:
    result = subprocess.run(
        ["uv", "build", "--no-build-isolation", "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    wheel = next(tmp_path.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        wheel_metadata = archive.read(next(n for n in names if n.endswith(".dist-info/METADATA")))
    assert any(name.endswith("ghostty_textual/py.typed") for name in names)
    assert any("licenses/LICENSE" in name for name in names)

    sdist = next(tmp_path.glob("*.tar.gz"))
    with tarfile.open(sdist) as archive:
        source_names = [name.split("/", 1)[1] for name in archive.getnames()]
        metadata_file = archive.extractfile(f"{sdist.name.removesuffix('.tar.gz')}/PKG-INFO")
        assert metadata_file is not None
        sdist_metadata = metadata_file.read()
    assert "src/ghostty_textual/py.typed" in source_names
    assert "tests/test_packaging.py" in source_names
    assert "LICENSE" in source_names
    assert not any(name.startswith((".ayder/", ".github/")) for name in source_names)
    assert "AGENTS.md" not in source_names

    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    dependencies = {str(Requirement(value)) for value in project["dependencies"]}
    for raw_metadata in (wheel_metadata, sdist_metadata):
        metadata = BytesParser().parsebytes(raw_metadata)
        assert metadata["Name"] == "ghostty-textual"
        assert metadata["Version"] == project["version"]
        assert metadata["Requires-Python"] == ">=3.12"
        assert metadata["License-Expression"] == "MIT"
        assert "LICENSE" in metadata.get_all("License-File", [])
        built_dependencies = {
            str(requirement)
            for value in metadata.get_all("Requires-Dist", [])
            if not (requirement := Requirement(value)).marker
            or "extra" not in str(requirement.marker)
        }
        assert built_dependencies == dependencies
