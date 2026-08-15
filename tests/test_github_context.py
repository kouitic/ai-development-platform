import json
from copy import deepcopy
from pathlib import Path

import pytest

from ai_dev_platform.config.loader import load_config
from ai_dev_platform.security.github_context import (
    GitHubContextError,
    load_trusted_development_context,
    load_trusted_github_context,
    load_trusted_quality_context,
)


def _event() -> dict[str, object]:
    return {
        "action": "synchronize",
        "number": 9,
        "repository": {
            "full_name": "owner/private-repo",
            "private": True,
            "visibility": "private",
        },
        "sender": {"login": "trusted-human"},
        "pull_request": {
            "number": 9,
            "head": {
                "ref": "ai/issue-9-safe",
                "sha": "a" * 40,
                "repo": {"full_name": "owner/private-repo", "fork": False},
            },
        },
    }


def _environment(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    event: dict[str, object],
) -> None:
    event_path = root / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_REPOSITORY": "owner/private-repo",
        "GITHUB_HEAD_REF": "ai/issue-9-safe",
        "GITHUB_ACTOR": "trusted-human",
        "GITHUB_WORKFLOW_REF": (
            "owner/private-repo/.github/workflows/ai-quality-gates.yml@refs/pull/9/merge"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_trusted_context_is_derived_from_payload(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch, initialized_project, _event())
    context = load_trusted_github_context(
        initialized_project, load_config(initialized_project).project.github
    )
    assert context.repository == "owner/private-repo"
    assert context.head_branch == "ai/issue-9-safe"
    assert context.pull_request_number == 9


@pytest.mark.parametrize("unsafe", ["public", "fork", "branch"])
def test_public_fork_and_unallowlisted_branch_are_rejected(
    initialized_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    event = deepcopy(_event())
    repository = event["repository"]
    pull_request = event["pull_request"]
    assert isinstance(repository, dict) and isinstance(pull_request, dict)
    head = pull_request["head"]
    assert isinstance(head, dict)
    if unsafe == "public":
        repository["private"] = False
        repository["visibility"] = "public"
    elif unsafe == "fork":
        head["repo"] = {"full_name": "attacker/fork", "fork": True}
    else:
        head["ref"] = "feature/unapproved"
        monkeypatch.setenv("GITHUB_HEAD_REF", "feature/unapproved")
    _environment(monkeypatch, initialized_project, event)
    if unsafe == "branch":
        monkeypatch.setenv("GITHUB_HEAD_REF", "feature/unapproved")
    with pytest.raises(GitHubContextError):
        load_trusted_github_context(
            initialized_project, load_config(initialized_project).project.github
        )


def test_required_environment_and_production_workflow_are_rejected(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _environment(monkeypatch, initialized_project, _event())
    workflow = initialized_project / ".github" / "workflows" / "ai-quality-gates.yml"
    source = workflow.read_text(encoding="utf-8")
    workflow.write_text(
        source.replace(
            "runs-on: ubuntu-latest", "runs-on: ubuntu-latest\n    environment: production"
        ),
        encoding="utf-8",
    )
    with pytest.raises(GitHubContextError):
        load_trusted_github_context(
            initialized_project, load_config(initialized_project).project.github
        )


def _quality_environment(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    issue: str = "12",
    pull_request: str = "9",
) -> None:
    event_path = root / "quality-event.json"
    event_path.write_text(
        json.dumps(
            {
                "inputs": {"issue": issue, "pull_request": pull_request},
                "repository": {
                    "full_name": "owner/private-repo",
                    "private": True,
                    "visibility": "private",
                    "default_branch": "main",
                },
                "sender": {"login": "trusted-human"},
            }
        ),
        encoding="utf-8",
    )
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "owner/private-repo",
        "GITHUB_ACTOR": "trusted-human",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_WORKFLOW_REF": (
            "owner/private-repo/.github/workflows/ai-quality-gates.yml@refs/heads/main"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_manual_quality_context_is_bound_to_actor_issue_pr_and_default_branch(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _quality_environment(monkeypatch, initialized_project)
    settings = load_config(initialized_project).project.github.model_copy(
        update={"allowed_actors": ["trusted-human"]}
    )

    context = load_trusted_quality_context(
        initialized_project,
        settings,
        issue_number=12,
        pull_request_number=9,
    )

    assert context.issue_number == 12
    assert context.pull_request_number == 9
    assert context.actor == "trusted-human"
    assert context.default_branch == "main"


@pytest.mark.parametrize("unsafe", ["issue", "pull_request", "branch", "actor"])
def test_manual_quality_context_rejects_mismatched_authority(
    initialized_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    _quality_environment(
        monkeypatch,
        initialized_project,
        issue="13" if unsafe == "issue" else "12",
        pull_request="10" if unsafe == "pull_request" else "9",
    )
    if unsafe == "branch":
        monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    allowed_actors = [] if unsafe == "actor" else ["trusted-human"]
    settings = load_config(initialized_project).project.github.model_copy(
        update={"allowed_actors": allowed_actors}
    )

    with pytest.raises(GitHubContextError):
        load_trusted_quality_context(
            initialized_project,
            settings,
            issue_number=12,
            pull_request_number=9,
        )


def _development_environment(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    issue: str = "12",
) -> None:
    event_path = root / "development-event.json"
    event_path.write_text(
        json.dumps(
            {
                "inputs": {"issue": issue},
                "repository": {
                    "full_name": "owner/private-repo",
                    "private": True,
                    "visibility": "private",
                    "default_branch": "main",
                },
                "sender": {"login": "trusted-human"},
            }
        ),
        encoding="utf-8",
    )
    values = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "owner/private-repo",
        "GITHUB_ACTOR": "trusted-human",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": "b" * 40,
        "GITHUB_WORKFLOW_REF": (
            "owner/private-repo/.github/workflows/ai-orchestrator.yml@refs/heads/main"
        ),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_manual_development_context_is_bound_to_actor_issue_and_default_branch(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _development_environment(monkeypatch, initialized_project)
    settings = load_config(initialized_project).project.github.model_copy(
        update={"allowed_actors": ["trusted-human"]}
    )

    context = load_trusted_development_context(initialized_project, settings, issue_number=12)

    assert context.issue_number == 12
    assert context.actor == "trusted-human"
    assert context.default_branch == "main"


@pytest.mark.parametrize("unsafe", ["issue", "branch", "actor"])
def test_manual_development_context_rejects_mismatched_authority(
    initialized_project: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe: str,
) -> None:
    _development_environment(
        monkeypatch,
        initialized_project,
        issue="13" if unsafe == "issue" else "12",
    )
    if unsafe == "branch":
        monkeypatch.setenv("GITHUB_REF", "refs/heads/feature")
    allowed_actors = [] if unsafe == "actor" else ["trusted-human"]
    settings = load_config(initialized_project).project.github.model_copy(
        update={"allowed_actors": allowed_actors}
    )

    with pytest.raises(GitHubContextError):
        load_trusted_development_context(initialized_project, settings, issue_number=12)
