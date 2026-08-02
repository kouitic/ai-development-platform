from pathlib import Path

import pytest

from ai_dev_platform.application.approval_service import record_decision
from ai_dev_platform.domain.models import TaskRecord, WorkflowState
from ai_dev_platform.infrastructure.github import GitHubError, MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import (
    SQLiteStateStore,
    StateConflictError,
    TaskNotFoundError,
)


def make_store(tmp_path: Path) -> SQLiteStateStore:
    return SQLiteStateStore(tmp_path / "state.sqlite3")


def test_state_round_trip_events_and_optimistic_conflict(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task = store.create_task(
        TaskRecord(task_id="issue-1", issue_number=1, commit_sha="abcdef123456")
    )
    loaded = store.get_task_by_issue(1)
    updated = store.save_task(
        loaded.model_copy(update={"state": WorkflowState.REQUIREMENTS_ANALYSIS})
    )
    assert updated.version == 1
    assert store.list_tasks()[0].state == WorkflowState.REQUIREMENTS_ANALYSIS
    with pytest.raises(StateConflictError):
        store.save_task(task.model_copy(update={"state": WorkflowState.REQUIREMENTS_ANALYSIS}))
    assert store.list_events(task.task_id)[0]["action"] == "task_created"
    with pytest.raises(TaskNotFoundError):
        store.get_task("does-not-exist")


def test_pause_resume_and_cancel_requests(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_task(TaskRecord(task_id="issue-2", issue_number=2, commit_sha="abcdef123456"))
    paused_requested = store.request_control(2, "pause")
    assert paused_requested.pause_requested
    paused = store.save_task(
        paused_requested.model_copy(
            update={
                "state": WorkflowState.PAUSED,
                "resume_state": WorkflowState.REQUIREMENTS_ANALYSIS,
                "pause_requested": False,
            }
        )
    )
    resumed = store.request_control(2, "resume")
    assert resumed.state == WorkflowState.REQUIREMENTS_ANALYSIS
    assert resumed.resume_state is None
    cancelled = store.request_control(2, "cancel")
    assert cancelled.cancel_requested
    with pytest.raises(ValueError):
        store.request_control(2, "unknown")
    assert paused.version < resumed.version < cancelled.version


def test_approval_is_bound_to_stage_and_commit(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task = store.create_task(
        TaskRecord(
            task_id="issue-3",
            issue_number=3,
            commit_sha="abcdef123456",
            state=WorkflowState.HUMAN_APPROVAL_REQUIRED,
        )
    )
    with pytest.raises(ValueError, match="commit"):
        record_decision(
            store,
            issue_number=3,
            stage="human-approval",
            commit_sha="0000000",
            approver="human",
            approved=True,
        )
    unchanged = record_decision(
        store,
        issue_number=3,
        stage="business-requirements",
        commit_sha=task.commit_sha,
        approver="human",
        approved=True,
        github_record_id="mock-comment-1",
    )
    assert unchanged.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    completed = record_decision(
        store,
        issue_number=3,
        stage="human-approval",
        commit_sha=task.commit_sha,
        approver="human",
        approved=True,
        reason="Reviewed evidence for this exact commit",
        github_record_id="mock-comment-2",
    )
    assert completed.state == WorkflowState.COMPLETED
    assert store.has_current_approval(3, "human-approval", task.commit_sha)


def test_rejection_returns_human_gate_to_rework(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task = store.create_task(
        TaskRecord(
            task_id="issue-4",
            issue_number=4,
            commit_sha="abcdef123456",
            state=WorkflowState.HUMAN_APPROVAL_REQUIRED,
        )
    )
    rejected = record_decision(
        store,
        issue_number=4,
        stage="human-approval",
        commit_sha=task.commit_sha,
        approver="human",
        approved=False,
        reason="Acceptance condition is not met",
        github_record_id="mock-comment-3",
    )
    assert rejected.state == WorkflowState.REWORK_REQUIRED


def test_human_gate_approval_is_not_recorded_before_gate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task = store.create_task(
        TaskRecord(task_id="issue-5", issue_number=5, commit_sha="abcdef123456")
    )
    with pytest.raises(ValueError, match="not waiting"):
        record_decision(
            store,
            issue_number=5,
            stage="human-approval",
            commit_sha=task.commit_sha,
            approver="human",
            approved=True,
        )
    assert not store.has_current_approval(5, "human-approval", task.commit_sha)


def test_github_record_failure_prevents_local_approval(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    task = store.create_task(
        TaskRecord(
            task_id="issue-6",
            issue_number=6,
            commit_sha="abcdef123456",
            state=WorkflowState.HUMAN_APPROVAL_REQUIRED,
        )
    )
    gateway = MockGitHubGateway(fail_with="simulated")
    with pytest.raises(GitHubError):
        record_decision(
            store,
            issue_number=6,
            stage="human-approval",
            commit_sha=task.commit_sha,
            approver="human",
            approved=True,
            gateway=gateway,
        )
    assert not store.has_current_approval(6, "human-approval", task.commit_sha)
    assert store.get_task(task.task_id).state == WorkflowState.HUMAN_APPROVAL_REQUIRED
