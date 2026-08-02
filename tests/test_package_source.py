import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from ai_dev_platform.application.package_service import package_source, verify_source_package


def commit_project(root: Path) -> None:
    for arguments in (
        ["init", "--initial-branch=main"],
        ["config", "user.email", "tests@example.invalid"],
        ["config", "user.name", "Test User"],
        ["add", "-A"],
        ["commit", "-m", "テスト用初期commit"],
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )


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
    commit_project(initialized_project)
    output = tmp_path / "source.zip"
    created = package_source(initialized_project, output)
    with zipfile.ZipFile(created) as archive:
        names = set(archive.namelist())
    assert "README.md" in names
    assert "source-package-manifest.json" in names
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
    commit_project(initialized_project)
    previous = package_source(initialized_project, initialized_project / "previous-source.zip")
    assert previous.exists()
    subprocess.run(
        ["git", "add", "-f", "previous-source.zip"],
        cwd=initialized_project,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "過去ZIPを含む状態"],
        cwd=initialized_project,
        check=True,
        capture_output=True,
    )

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


def test_formal_source_package_rejects_dirty_git_status(
    initialized_project: Path, tmp_path: Path
) -> None:
    (initialized_project / "tests").mkdir(exist_ok=True)
    (initialized_project / "tests" / "test_example.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8"
    )
    commit_project(initialized_project)
    (initialized_project / "README.md").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="clean Git status"):
        package_source(initialized_project, tmp_path / "dirty.zip")


def test_source_package_manifest_and_digest_are_verified(
    initialized_project: Path, tmp_path: Path
) -> None:
    (initialized_project / "tests").mkdir(exist_ok=True)
    (initialized_project / "tests" / "test_example.py").write_text(
        "def test_ok(): assert True\n", encoding="utf-8"
    )
    commit_project(initialized_project)
    archive_path = package_source(initialized_project, tmp_path / "source.zip")

    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("source-package-manifest.json"))
    assert manifest["git_status_clean"] is True
    assert len(manifest["commit_sha"]) == 40
    assert manifest["files"]
    assert len(manifest["package_digest"]) == 64

    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(tampered, "w") as target:
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "README.md":
                data += b"tampered"
            target.writestr(item, data)
    with pytest.raises(ValueError, match="file digest mismatch"):
        verify_source_package(tampered)

    bad_package_digest = tmp_path / "bad-package-digest.zip"
    with (
        zipfile.ZipFile(archive_path) as source,
        zipfile.ZipFile(bad_package_digest, "w") as target,
    ):
        for item in source.infolist():
            data = source.read(item.filename)
            if item.filename == "source-package-manifest.json":
                changed_manifest = json.loads(data)
                changed_manifest["package_digest"] = "0" * 64
                data = json.dumps(changed_manifest).encode("utf-8")
            target.writestr(item, data)
    with pytest.raises(ValueError, match="package digest mismatch"):
        verify_source_package(bad_package_digest)


def test_source_package_requires_git_and_a_manifest(tmp_path: Path) -> None:
    root = tmp_path / "not-a-repository"
    root.mkdir()
    (root / "README.md").write_text("source\n", encoding="utf-8")
    with pytest.raises(ValueError, match="valid Git repository"):
        package_source(root, tmp_path / "source.zip")

    missing_manifest = tmp_path / "missing-manifest.zip"
    with zipfile.ZipFile(missing_manifest, "w") as archive:
        archive.writestr("README.md", "source")
        archive.writestr("tests/test_ok.py", "def test_ok(): pass")
        archive.writestr("docs/guide.md", "guide")
        archive.writestr(".ai-dev/project.yaml", "schema_version: '1.0'")
    with pytest.raises(ValueError, match="manifest is missing"):
        verify_source_package(missing_manifest)


def test_source_package_rejects_duplicate_archive_paths(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("README.md", "first")
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr("README.md", "second")
    with pytest.raises(ValueError, match="duplicate paths"):
        verify_source_package(duplicate)
