"""Create a clean source-only ZIP with an explicit inclusion policy."""

from __future__ import annotations

import zipfile
from pathlib import Path

from ai_dev_platform.security.paths import production_like_files
from ai_dev_platform.security.scanner import scan_tree

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


def package_source(root: Path, output: Path | None = None) -> Path:
    """Build a deterministic source ZIP after Secret and data-policy checks."""
    root = root.resolve()
    findings = scan_tree(root)
    if findings:
        raise ValueError("source package blocked because secret-like content was detected")
    if production_like_files(root):
        raise ValueError("source package blocked because production-like data was detected")
    destination = (output or (root.parent / f"{root.name}-source.zip")).resolve()
    if destination.exists():
        raise FileExistsError("source package destination already exists")
    files = [
        path
        for path in root.rglob("*")
        if path.is_file() and _included(path.relative_to(root)) and path.resolve() != destination
    ]
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
            relative = path.relative_to(root).as_posix()
            data = path.read_bytes()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data)
    try:
        verify_source_package(destination)
    except ValueError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def verify_source_package(archive_path: Path) -> list[str]:
    """Reject forbidden paths and require source, tests, config, and documentation."""
    archive_path = archive_path.resolve()
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            entries = archive.infolist()
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError("source package is not a readable ZIP") from exc
    names = [entry.filename for entry in entries if not entry.is_dir()]
    if not names or any(_forbidden_archive_name(name) for name in names):
        raise ValueError("source package contains a forbidden or unsafe path")
    for entry in entries:
        unix_mode = entry.external_attr >> 16
        if unix_mode & 0o170000 == 0o120000:
            raise ValueError("source package must not contain symbolic links")
    required = {
        "README.md": any(name == "README.md" for name in names),
        "tests": any(name.startswith("tests/") for name in names),
        "configuration": any(name.startswith(".ai-dev/") for name in names),
        "documentation": any(name.startswith("docs/") for name in names),
    }
    missing = [label for label, present in required.items() if not present]
    if missing:
        raise ValueError("source package is missing required content: " + ", ".join(missing))
    return names
