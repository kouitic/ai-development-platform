"""Path and production-like data guards."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

DEFAULT_FORBIDDEN_READS = (
    ".env",
    ".env.*",
    "**/.aws/credentials",
    "**/.ssh/**",
    "**/.config/gh/hosts.yml",
    ".ai-dev/credentials/**",
    "restricted-test-data/**",
    "production-like-data/**",
)

PRODUCTION_LIKE_DIRECTORIES = (
    "restricted-test-data",
    "production-like-data",
    "artifacts/private",
)


def normalize_relative(root: Path, candidate: Path) -> PurePosixPath:
    """Resolve a candidate and ensure it remains under root."""
    resolved_root = root.resolve()
    resolved = candidate.resolve()
    try:
        relative = resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise PermissionError("path escapes the project root") from exc
    return PurePosixPath(relative.as_posix())


def matches_any(path: PurePosixPath, patterns: list[str] | tuple[str, ...]) -> bool:
    """Return whether a normalized path matches a policy glob."""
    return any(
        path.match(pattern) or str(path) == (pattern[:-3] if pattern.endswith("/**") else pattern)
        for pattern in patterns
    )


def assert_read_allowed(root: Path, candidate: Path, patterns: list[str] | None = None) -> None:
    """Reject credential and production-like data reads."""
    relative = normalize_relative(root, candidate)
    if matches_any(relative, patterns or list(DEFAULT_FORBIDDEN_READS)):
        raise PermissionError("reading this protected path is not permitted")


def assert_write_allowed(
    root: Path,
    candidate: Path,
    writable_patterns: list[str],
    protected_patterns: list[str],
    *,
    protected_path_approved: bool = False,
) -> PurePosixPath:
    """Enforce repository, symlink, writable-scope, and protected-path boundaries."""
    relative = normalize_relative(root, candidate)
    if not matches_any(relative, writable_patterns):
        raise PermissionError("path is outside the configured writable scope")
    if matches_any(relative, protected_patterns) and not protected_path_approved:
        raise PermissionError("protected path requires explicit human approval")
    return relative


def production_like_files(root: Path) -> list[Path]:
    """List files in directories forbidden from normal source control."""
    files: list[Path] = []
    for dirname in PRODUCTION_LIKE_DIRECTORIES:
        directory = root / dirname
        if directory.exists():
            files.extend(path.relative_to(root) for path in directory.rglob("*") if path.is_file())
    return files
