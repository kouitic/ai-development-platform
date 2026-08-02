import zipfile
from pathlib import Path

import pytest

from ai_dev_platform.application.package_service import package_source, verify_source_package


def test_source_package_contains_only_reviewed_source_inputs(
    initialized_project: Path, tmp_path: Path
) -> None:
    (initialized_project / ".venv").mkdir()
    (initialized_project / ".venv" / "secret.bin").write_bytes(b"excluded")
    (initialized_project / "dist").mkdir()
    (initialized_project / "dist" / "wheel.whl").write_bytes(b"excluded")
    (initialized_project / ".uv-cache-old").mkdir()
    (initialized_project / ".uv-cache-old" / "cache.bin").write_bytes(b"excluded")
    (initialized_project / ".pytest_cache").mkdir()
    (initialized_project / ".pytest_cache" / "state").write_bytes(b"excluded")
    (initialized_project / "build").mkdir()
    (initialized_project / "build" / "artifact.bin").write_bytes(b"excluded")
    (initialized_project / "docs" / "previous-source.zip").write_bytes(b"excluded")
    (initialized_project / "tests").mkdir()
    (initialized_project / "tests" / "test_example.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8"
    )
    output = tmp_path / "source.zip"
    created = package_source(initialized_project, output)
    with zipfile.ZipFile(created) as archive:
        names = set(archive.namelist())
    assert "README.md" in names
    assert "tests/test_example.py" in names
    assert not any(".venv" in name or name.startswith("dist/") for name in names)
    assert not any(
        "__pycache__" in name
        or name.endswith(".pyc")
        or name.endswith(".zip")
        or name.startswith(("build/", ".pytest_cache/", ".uv-cache"))
        for name in names
    )
    with pytest.raises(FileExistsError):
        package_source(initialized_project, output)


def test_repackaging_never_nests_an_older_source_zip(
    initialized_project: Path, tmp_path: Path
) -> None:
    (initialized_project / "tests").mkdir(exist_ok=True)
    (initialized_project / "tests" / "test_example.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8"
    )
    previous = package_source(initialized_project, initialized_project / "previous-source.zip")
    assert previous.exists()

    current = package_source(initialized_project, tmp_path / "current-source.zip")
    with zipfile.ZipFile(current) as archive:
        names = archive.namelist()
    assert not any(name.lower().endswith(".zip") for name in names)


def test_source_package_verifier_rejects_nested_zip(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("README.md", "readme")
        archive.writestr("tests/test_ok.py", "def test_ok(): pass")
        archive.writestr("docs/guide.md", "guide")
        archive.writestr(".ai-dev/project.yaml", "schema_version: '1.0'")
        archive.writestr("docs/previous.zip", b"unknown archive")
    with pytest.raises(ValueError, match="forbidden"):
        verify_source_package(archive_path)
