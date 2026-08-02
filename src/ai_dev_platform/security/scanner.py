"""Conservative scanners that report categories without echoing sensitive values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class Finding:
    """A location and category; the matched value is deliberately omitted."""

    path: Path
    line: int
    category: str


SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{16,}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    (
        "password_value",
        re.compile(
            r"(?im)^\s*(?:password|passwd|pwd)\s*[:=]\s*"
            r"(?![\s'\"]*(?:$|change_me|replace_me|example|dummy|not_configured))["
            r"^\s#]{8,}"
        ),
    ),
    (
        "database_url_with_credentials",
        re.compile(r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s:/]+:[^\s@]+@"),
    ),
)

EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pip-audit-cache",
    ".pytest-tmp",
    ".pytest_cache",
    ".ruff_cache",
    ".uv-cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "runtime",
}
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".env",
    ".ini",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


def _scan_file(root: Path, path: Path) -> list[Finding]:
    """Scan one plain-text candidate without expanding archives or exposing values."""
    if path.suffix.lower() == ".zip":
        return []
    if path.name == ".env.example":
        pass
    elif path.suffix.lower() not in TEXT_SUFFIXES and not path.name.startswith(".env"):
        return []
    try:
        if path.stat().st_size > 1_000_000:
            return []
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = path
    return scan_text(text, relative)


def _tree_path_is_excluded(root: Path, path: Path) -> bool:
    relative = path.relative_to(root)
    parts = relative.parts
    return (
        path.suffix.lower() == ".zip"
        or any(
            part in EXCLUDED_PARTS
            or part.startswith((".pip-audit-cache", ".uv-bin", ".uv-cache", ".uv-tools"))
            for part in parts
        )
        or (len(parts) >= 2 and parts[0] == ".ai-dev" and parts[1] == "local")
    )


def scan_text(text: str, path: Path = Path("<memory>")) -> list[Finding]:
    """Scan text and return redacted findings."""
    findings: list[Finding] = []
    for category, pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            findings.append(Finding(path=path, line=line, category=category))
    return findings


def scan_tree(root: Path) -> list[Finding]:
    """Scan likely-text project files while skipping runtime and VCS internals."""
    findings: list[Finding] = []
    if not root.exists():
        return findings
    for path in root.rglob("*"):
        if not path.is_file() or _tree_path_is_excluded(root, path):
            continue
        findings.extend(_scan_file(root, path))
    return findings


def scan_files(root: Path, relative_paths: list[str]) -> list[Finding]:
    """Scan explicit Git candidates even when their directory is excluded from tree scans."""
    root = root.resolve()
    findings: list[Finding] = []
    for relative_path in sorted(set(relative_paths)):
        candidate = (root / relative_path).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError:
            continue
        if not candidate.is_file():
            continue
        findings.extend(_scan_file(root, candidate))
    return findings


def redact(text: str) -> str:
    """Mask known secret patterns without retaining their values."""
    redacted = text
    for category, pattern in SECRET_PATTERNS:
        redacted = pattern.sub(f"[REDACTED:{category}]", redacted)
    return redacted


class SensitiveContentError(ValueError):
    """Raised when content is unsafe to persist or transmit."""


def ensure_safe_to_persist(text: str) -> None:
    """Reject content containing a recognized secret."""
    if scan_text(text):
        raise SensitiveContentError("sensitive content detected; value was not persisted")
