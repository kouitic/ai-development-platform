"""Safe, all-or-nothing project template initialization."""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path


@dataclass(slots=True)
class InitResult:
    """Created relative file paths."""

    created: list[Path]


class InitConflictError(FileExistsError):
    """Initialization would overwrite existing files."""

    def __init__(self, conflicts: list[Path]) -> None:
        super().__init__("initialization conflicts with existing files")
        self.conflicts = conflicts


def _walk(node: Traversable, prefix: Path = Path()) -> list[tuple[Path, Traversable]]:
    result: list[tuple[Path, Traversable]] = []
    for child in node.iterdir():
        relative = prefix / child.name
        if child.is_dir():
            result.extend(_walk(child, relative))
        else:
            result.append((relative, child))
    return result


def initialize_project(root: Path, project_name: str) -> InitResult:
    """Copy packaged templates without overwriting any existing file."""
    root = root.resolve()
    template_root = files("ai_dev_platform").joinpath("templates", "project")
    entries = _walk(template_root)
    conflicts = sorted(relative for relative, _ in entries if (root / relative).exists())
    if conflicts:
        raise InitConflictError(conflicts)

    created: list[Path] = []
    try:
        for relative, source in entries:
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = source.read_text(encoding="utf-8").replace("{{ project_name }}", project_name)
            target.write_text(content, encoding="utf-8", newline="\n")
            created.append(relative)
    except Exception:
        for relative in reversed(created):
            with suppress(OSError):
                (root / relative).unlink()
        raise
    return InitResult(created=created)
