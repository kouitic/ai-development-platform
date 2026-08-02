from pathlib import Path

import pytest

from ai_dev_platform.application.context_builder import TaskContextBuilder
from ai_dev_platform.application.deployment import (
    DEPLOYMENT_QUESTIONS,
    approve_deployment_configuration,
    record_answer,
    start_deployment_session,
    unanswered_questions,
)
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    Decision,
    EvidenceReference,
    RequirementsResult,
    TaskEvidence,
    TaskRecord,
    WorkflowState,
)
from ai_dev_platform.infrastructure.git import MockGitWorktree
from ai_dev_platform.infrastructure.github import MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.security.scanner import SensitiveContentError


def requirements() -> RequirementsResult:
    return RequirementsResult(
        decision=Decision.PASS,
        summary="confirmed",
        evidence=[EvidenceReference(id="issue", kind="github", reference="mock://issues/1")],
        business_requirements=["BR-001: notify a user"],
        acceptance_criteria=["AC-001: notification is observable"],
        scope=["notification"],
        out_of_scope=["production deployment"],
        human_decisions=["deployment target"],
    )


def test_issue_requirements_findings_diff_and_environment_reach_expected_context(
    initialized_project: Path,
) -> None:
    loaded = load_config(initialized_project)
    github = MockGitHubGateway()
    github.issues[1] = {
        "title": "Notify users",
        "body": "Issue body with AC-001",
        "labels": ["ai:ready"],
    }
    github.pull_requests[2] = {
        "number": 2,
        "title": "PR",
        "body": "",
        "head_branch": "ai/issue-1-notify",
        "base_branch": "main",
        "head_sha": "a" * 40,
        "url": "mock://pulls/2",
    }
    github.pull_request_diffs[2] = "diff --git a/src/app.py b/src/app.py"
    task = TaskRecord(
        task_id="issue-1",
        issue_number=1,
        commit_sha="a" * 40,
        branch="ai/issue-1-notify",
        pull_request_number=2,
        evidence=TaskEvidence(requirements_result=requirements()),
        context={"security_scan_results": ["passed"]},
    )
    git = MockGitWorktree(branch=task.branch, files=["src/app.py"], diff_text="local diff")
    builder = TaskContextBuilder(initialized_project, github=github, git=git)

    developer = builder.build(task, WorkflowState.IMPLEMENTING, loaded.agents["developer"])
    assert developer["issue_body"] == "Issue body with AC-001"
    assert developer["acceptance_criteria"] == ["AC-001: notification is observable"]
    assert developer["current_git_diff"] == "local diff"

    system = builder.build(task, WorkflowState.SYSTEM_REVIEW, loaded.agents["system-reviewer"])
    assert system["pull_request_diff"].startswith("diff --git")
    assert system["changed_files"] == []


def test_secret_in_issue_is_rejected_before_agent_context(initialized_project: Path) -> None:
    loaded = load_config(initialized_project)
    github = MockGitHubGateway()
    github.issues[2] = {
        "title": "Unsafe",
        "body": "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
        "labels": [],
    }
    task = TaskRecord(task_id="issue-2", issue_number=2, commit_sha="abcdef123456")
    builder = TaskContextBuilder(initialized_project, github=github)
    with pytest.raises(SensitiveContentError):
        builder.build(task, WorkflowState.REQUIREMENTS_ANALYSIS, loaded.agents["conversation"])


def test_deployment_answers_are_not_reasked_and_approval_resumes_task(
    initialized_project: Path,
) -> None:
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "deployment.sqlite3")
    store.create_task(
        TaskRecord(
            task_id="issue-3",
            issue_number=3,
            commit_sha="abcdef123456",
            state=WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED,
        )
    )
    session = start_deployment_session("test-project", 3)
    store.save_conversation_session(session)
    first = DEPLOYMENT_QUESTIONS[0]
    session = record_answer(
        store,
        session,
        question_id=first.id,
        answer=first.recommendation,
        answered_by="human",
    )
    same = record_answer(
        store,
        session,
        question_id=first.id,
        answer="duplicate",
        answered_by="human",
    )
    assert same.answers == session.answers
    assert first.id not in {question.id for question in unanswered_questions(session)}
    with pytest.raises(ValueError, match="all deployment questions"):
        approve_deployment_configuration(
            store, session, approver="human", github_record_id="mock-comment-1"
        )

    for question in unanswered_questions(session):
        session = record_answer(
            store,
            session,
            question_id=question.id,
            answer=question.recommendation,
            answered_by="human",
        )
    task = approve_deployment_configuration(
        store, session, approver="human", github_record_id="mock-comment-2"
    )
    assert task.state == WorkflowState.DEPLOYMENT_CONFIGURATION
    assert task.evidence.deployment_configuration is not None
    assert task.evidence.deployment_configuration.human_approved

    loaded = load_config(initialized_project)
    context = TaskContextBuilder(initialized_project).build(
        task, WorkflowState.DESIGNING, loaded.agents["developer"]
    )
    assert context["deployment_configuration"]["human_approved"] is True
