"""Derive real-provider trust from GitHub payload and checked-in workflow context."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

from ai_dev_platform.domain.models import GitHubSettings


class GitHubContextError(ValueError):
    """GitHub execution context is absent, inconsistent, or unsafe."""


@dataclass(frozen=True, slots=True)
class TrustedGitHubContext:
    """Minimal validated context safe to use for provider authorization."""

    repository: str
    actor: str
    event_name: str
    action: str
    pull_request_number: int
    head_repository: str
    head_branch: str
    head_sha: str
    workflow_path: str


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GitHubContextError(f"GitHub payload is missing {label}")
    return {str(key): item for key, item in value.items()}


def _read_event_payload() -> dict[str, Any]:
    event_path = os.getenv("GITHUB_EVENT_PATH", "")
    if not event_path:
        raise GitHubContextError("GitHub event payload is required")
    path = Path(event_path)
    try:
        if path.stat().st_size > 1_000_000:
            raise GitHubContextError("GitHub event payload is unexpectedly large")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GitHubContextError("GitHub event payload is unreadable") from exc
    return _mapping(payload, "root")


def _workflow_has_safe_context(root: Path) -> str:
    workflow_path = ".github/workflows/ai-quality-gates.yml"
    workflow_ref = os.getenv("GITHUB_WORKFLOW_REF", "")
    if f"{workflow_path}@" not in workflow_ref.replace("\\", "/"):
        raise GitHubContextError("only the integrated quality workflow may run Claude")
    path = root / workflow_path
    try:
        source = path.read_text(encoding="utf-8")
        parsed = yaml.safe_load(source)
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise GitHubContextError("integrated quality workflow is unreadable") from exc
    workflow = _mapping(parsed, "workflow")
    jobs = _mapping(workflow.get("jobs"), "workflow jobs")
    for raw_job in jobs.values():
        job = _mapping(raw_job, "workflow job")
        if "environment" in job:
            raise GitHubContextError("Claude quality workflow must not use a GitHub Environment")
    lowered = f"{workflow_ref}\n{source}".lower()
    if any(marker in lowered for marker in ("production", "prod-deploy", "deploy-production")):
        raise GitHubContextError("production workflows cannot run the development provider")
    return workflow_path


def load_trusted_github_context(
    root: Path,
    settings: GitHubSettings,
) -> TrustedGitHubContext:
    """Validate payload-derived repository, PR, branch, actor, and workflow constraints."""
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise GitHubContextError("real Claude execution requires GitHub Actions")
    payload = _read_event_payload()
    event_name = os.getenv("GITHUB_EVENT_NAME", "")
    if event_name != "pull_request" or "pull_request" not in payload:
        raise GitHubContextError("only pull_request events are allowed")
    action = str(payload.get("action", ""))
    if action not in {"opened", "synchronize", "reopened"}:
        raise GitHubContextError("Pull Request action is not allowed")

    repository = _mapping(payload.get("repository"), "repository")
    repository_name = str(repository.get("full_name", ""))
    if repository.get("private") is not True or str(repository.get("visibility", "")) != "private":
        raise GitHubContextError("real Claude execution requires a private repository")
    if not repository_name or os.getenv("GITHUB_REPOSITORY") != repository_name:
        raise GitHubContextError("repository context does not match the event payload")

    pull_request = _mapping(payload.get("pull_request"), "pull_request")
    head = _mapping(pull_request.get("head"), "pull_request.head")
    head_repository = _mapping(head.get("repo"), "pull_request.head.repo")
    head_repository_name = str(head_repository.get("full_name", ""))
    if head_repository_name != repository_name or head_repository.get("fork") is True:
        raise GitHubContextError("fork or foreign-head Pull Requests cannot run Claude")
    head_branch = str(head.get("ref", ""))
    if not head_branch or not any(
        fnmatch(head_branch, pattern) for pattern in settings.allowed_branch_patterns
    ):
        raise GitHubContextError("Pull Request head branch is not allowlisted")
    if os.getenv("GITHUB_HEAD_REF") != head_branch:
        raise GitHubContextError("head branch context does not match the event payload")

    sender = _mapping(payload.get("sender"), "sender")
    actor = str(sender.get("login", ""))
    if not actor or os.getenv("GITHUB_ACTOR") != actor or actor.endswith("[bot]"):
        raise GitHubContextError("Pull Request actor is absent, inconsistent, or automated")
    if settings.allowed_actors and actor not in settings.allowed_actors:
        raise GitHubContextError("Pull Request actor is not allowlisted")

    head_sha = str(head.get("sha", ""))
    number = pull_request.get("number", payload.get("number"))
    if not isinstance(number, int) or number < 1 or len(head_sha) < 7:
        raise GitHubContextError("Pull Request identity is invalid")
    workflow_path = _workflow_has_safe_context(root.resolve())
    return TrustedGitHubContext(
        repository=repository_name,
        actor=actor,
        event_name=event_name,
        action=action,
        pull_request_number=number,
        head_repository=head_repository_name,
        head_branch=head_branch,
        head_sha=head_sha,
        workflow_path=workflow_path,
    )
