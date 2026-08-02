"""Structured Issue requirement parsing for formal quality gates."""

from __future__ import annotations

import re
from typing import Any

import yaml
from pydantic import ValidationError

from ai_dev_platform.domain.models import RequirementItem

_YAML_BLOCK = re.compile(r"```ya?ml\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)


def parse_structured_issue_requirements(
    body: str,
    *,
    source_reference: str,
) -> list[RequirementItem]:
    """Read the first fenced YAML block containing a ``requirements`` list."""
    candidate: Any = None
    for block in _YAML_BLOCK.findall(body):
        try:
            loaded = yaml.safe_load(block)
        except yaml.YAMLError:
            continue
        if isinstance(loaded, dict) and "requirements" in loaded:
            candidate = loaded["requirements"]
            break
    if not isinstance(candidate, list) or not candidate:
        raise ValueError("Issue must contain a non-empty fenced YAML requirements section")

    normalized: list[dict[str, Any]] = []
    for value in candidate:
        if not isinstance(value, dict):
            raise ValueError("each structured Issue requirement must be an object")
        item = dict(value)
        item.setdefault("source_reference", source_reference)
        normalized.append(item)
    try:
        requirements = [RequirementItem.model_validate(value) for value in normalized]
    except ValidationError as exc:
        raise ValueError("structured Issue requirements are invalid") from exc
    ids = [requirement.id for requirement in requirements]
    if len(ids) != len(set(ids)):
        raise ValueError("structured Issue requirement IDs must be unique")
    return requirements
