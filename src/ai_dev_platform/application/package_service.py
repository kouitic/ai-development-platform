"""Create and verify a clean, manifest-bound source-only ZIP."""

from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from ai_dev_platform.domain.models import (
    SourcePackageFile,
    SourcePackageManifest,
)
from ai_dev_platform.security.paths import production_like_files
from ai_dev_platform.security.scanner import ensure_safe_to_persist, scan_tree

_MANIFEST_NAME = "source-package-manifest.json"
_ROOT_FILES = {
    ".env.example",
    ".gitignore",
    ".pre-commit-config.yaml",
    "AGENTS.md",
    "CODEOWNERS",
    "LICENSE",
    "README.md",
    "pyproject.toml",
    "uv.lock",
}
_ROOT_DIRECTORIES = {".ai-dev", ".github", "docs", "evaluation", "scripts", "src", "tests"}
_EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".pytest-tmp",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "local",
}


def _forbidden_archive_name(name: str) -> bool:
    relative = Path(name)
    return (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or any(
            part in _EXCLUDED_PARTS or part.startswith(".uv-cache") or part.endswith(".egg-info")
            for part in relative.parts
        )
        or relative.name == ".coverage"
        or relative.suffix.lower() in {".pyc", ".pyo", ".zip"}
    )


def _included(relative: Path) -> bool:
    if not relative.parts:
        return False
    if any(
        part in _EXCLUDED_PARTS or part.startswith(".uv-cache") or part.endswith(".egg-info")
        for part in relative.parts
    ):
        return False
    if relative.name == ".coverage" or relative.suffix.lower() in {".pyc", ".pyo", ".zip"}:
        return False
    return relative.name in _ROOT_FILES or relative.parts[0] in _ROOT_DIRECTORIES


def _run_git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-c", f"safe.directory={root.as_posix()}", *arguments],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("source package Git provenance could not be collected") from exc
    if completed.returncode != 0:
        raise ValueError("source package requires a valid Git repository")
    return completed.stdout.strip()


def _clean_git_commit(root: Path) -> str:
    status = _run_git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        dirty_files = sorted(line[3:].strip() for line in status.splitlines() if len(line) >= 4)
        preview = ", ".join(dirty_files[:10])
        suffix = " ..." if len(dirty_files) > 10 else ""
        raise ValueError(f"formal source package requires a clean Git status: {preview}{suffix}")
    commit_sha = _run_git(root, "rev-parse", "HEAD")
    if len(commit_sha) != 40 or any(
        character not in "0123456789abcdef" for character in commit_sha
    ):
        raise ValueError("source package commit SHA is invalid")
    return commit_sha


def _package_digest(
    commit_sha: str,
    git_status_clean: bool,
    generated_at: datetime,
    files: list[SourcePackageFile],
) -> str:
    canonical = json.dumps(
        {
            "commit_sha": commit_sha,
            "git_status_clean": git_status_clean,
            "generated_at": generated_at.isoformat(),
            "files": [
                item.model_dump(mode="json") for item in sorted(files, key=lambda item: item.path)
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _zip_info(path: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    return info


def package_source(root: Path, output: Path | None = None) -> Path:
    """Build a formal source ZIP from an exact clean Git commit."""
    root = root.resolve()
    destination = (output or (root.parent / f"{root.name}-source.zip")).resolve()
    if destination.exists():
        raise FileExistsError("source package destination already exists")
    commit_sha = _clean_git_commit(root)
    findings = scan_tree(root)
    if findings:
        raise ValueError("source package blocked because secret-like content was detected")
    if production_like_files(root):
        raise ValueError("source package blocked because production-like data was detected")
    paths = sorted(
        [
            path
            for path in root.rglob("*")
            if path.is_file()
            and _included(path.relative_to(root))
            and path.resolve() != destination
        ],
        key=lambda item: item.relative_to(root).as_posix(),
    )
    file_entries = [
        SourcePackageFile(
            path=path.relative_to(root).as_posix(),
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in paths
    ]
    generated_at = datetime.now(UTC)
    manifest = SourcePackageManifest(
        commit_sha=commit_sha,
        git_status_clean=True,
        generated_at=generated_at,
        files=file_entries,
        package_digest=_package_digest(commit_sha, True, generated_at, file_entries),
    )
    manifest_bytes = json.dumps(
        manifest.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ensure_safe_to_persist(manifest_bytes.decode("utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, entry in zip(paths, file_entries, strict=True):
            archive.writestr(_zip_info(entry.path), path.read_bytes())
        archive.writestr(_zip_info(_MANIFEST_NAME), manifest_bytes)
    try:
        verify_source_package(destination)
    except ValueError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def verify_source_package(archive_path: Path) -> list[str]:
    """Verify paths, source hashes, clean provenance, and the package digest."""
    archive_path = archive_path.resolve()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            entries = archive.infolist()
            names = [entry.filename for entry in entries if not entry.is_dir()]
            if len(names) != len(set(names)):
                raise ValueError("source package contains duplicate paths")
            if not names or any(_forbidden_archive_name(name) for name in names):
                raise ValueError("source package contains a forbidden or unsafe path")
            for entry in entries:
                unix_mode = entry.external_attr >> 16
                if unix_mode & 0o170000 == 0o120000:
                    raise ValueError("source package must not contain symbolic links")
            if names.count(_MANIFEST_NAME) != 1:
                raise ValueError("source package manifest is missing")
            try:
                manifest = SourcePackageManifest.model_validate_json(archive.read(_MANIFEST_NAME))
            except ValueError as exc:
                raise ValueError("source package manifest is invalid") from exc
            if not manifest.git_status_clean:
                raise ValueError("formal source package manifest is not clean")
            source_names = [name for name in names if name != _MANIFEST_NAME]
            if source_names != [item.path for item in manifest.files]:
                raise ValueError("source package manifest file list does not match the archive")
            verified_files = [
                SourcePackageFile(
                    path=name,
                    sha256=hashlib.sha256(archive.read(name)).hexdigest(),
                )
                for name in source_names
            ]
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("source package is not a readable ZIP") from exc
    if verified_files != manifest.files:
        raise ValueError("source package file digest mismatch")
    if manifest.package_digest != _package_digest(
        manifest.commit_sha,
        manifest.git_status_clean,
        manifest.generated_at,
        verified_files,
    ):
        raise ValueError("source package digest mismatch")
    required = {
        "README.md": "README.md" in names,
        "tests": any(name.startswith("tests/") for name in names),
        "configuration": any(name.startswith(".ai-dev/") for name in names),
        "documentation": any(name.startswith("docs/") for name in names),
    }
    missing = [label for label, present in required.items() if not present]
    if missing:
        raise ValueError("source package is missing required content: " + ", ".join(missing))
    return names
