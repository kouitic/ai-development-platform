"""Structured Issue requirement parsing for formal quality gates."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import yaml
from pydantic import ValidationError

from ai_dev_platform.domain.models import (
    IssueComment,
    RequirementItem,
    RequirementsApproval,
)

_YAML_BLOCK = re.compile(r"```ya?ml\s*(.*?)```", flags=re.IGNORECASE | re.DOTALL)
_APPROVAL_STATUS = re.compile(r"^ai-dev 要件承認:\s*(承認|却下)\s*$", re.MULTILINE)
_APPROVAL_DIGEST = re.compile(r"^要件ダイジェスト:\s*([0-9a-f]{64})\s*$", re.MULTILINE)


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


def requirements_digest(requirements: list[RequirementItem]) -> str:
    """Return a canonical digest of formal requirement content from an Issue."""
    normalized = [
        {
            "id": requirement.id,
            "type": requirement.type.value,
            "description": requirement.description,
            "acceptance_criteria": requirement.acceptance_criteria,
            "required": requirement.required,
        }
        for requirement in sorted(requirements, key=lambda item: item.id)
    ]
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def find_requirements_approval(
    issue_number: int,
    requirements: list[RequirementItem],
    comments: list[IssueComment],
) -> RequirementsApproval | None:
    """Return the latest human GitHub approval whose digest matches the Issue."""
    expected_digest = requirements_digest(requirements)
    decisions: list[tuple[IssueComment, str]] = []
    for comment in comments:
        if comment.author_is_bot:
            continue
        status = _APPROVAL_STATUS.search(comment.body)
        digest = _APPROVAL_DIGEST.search(comment.body)
        if status is None or digest is None:
            continue
        if digest.group(1) != expected_digest:
            continue
        decisions.append((comment, status.group(1)))
    if not decisions:
        return None
    latest_comment, latest_status = max(
        decisions,
        key=lambda item: item[0].created_at,
    )
    if latest_status != "承認":
        return None
    return RequirementsApproval(
        issue_number=issue_number,
        requirements_digest=expected_digest,
        approved_by=latest_comment.author,
        approved_at=latest_comment.created_at,
        github_reference=latest_comment.url,
    )
