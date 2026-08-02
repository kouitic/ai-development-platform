"""Explicit, commit-bound approval and rejection handling."""

from __future__ import annotations

from ai_dev_platform.domain.models import ApprovalRecord, TaskRecord, WorkflowState
from ai_dev_platform.domain.workflow import assert_transition
from ai_dev_platform.infrastructure.github import GitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore

HUMAN_GATE_STAGE = "human-approval"
QA_CONDITIONS_STAGE = "qa-conditions"
REQUIREMENTS_STAGE = "requirements"


def record_decision(
    store: SQLiteStateStore,
    *,
    issue_number: int,
    stage: str,
    commit_sha: str,
    approver: str,
    approved: bool,
    reason: str = "",
    conditions: list[str] | None = None,
    pull_request_number: int | None = None,
    github_record_id: str | None = None,
    gateway: GitHubGateway | None = None,
) -> TaskRecord:
    """Record a commit-bound decision only after GitHub has a formal record."""
    task = store.get_task_by_issue(issue_number)
    if commit_sha != task.commit_sha:
        raise ValueError("approval commit does not match the task's current commit")
    if len(commit_sha) < 7:
        raise ValueError("a commit SHA of at least 7 characters is required")
    if stage == HUMAN_GATE_STAGE and task.state != WorkflowState.HUMAN_APPROVAL_REQUIRED:
        raise ValueError("task is not waiting at the human approval gate")
    if stage == REQUIREMENTS_STAGE and task.state != WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED:
        raise ValueError("task is not waiting at the requirements approval gate")
    if (
        stage == QA_CONDITIONS_STAGE
        and task.state != WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED
    ):
        raise ValueError("task is not waiting at the QA conditional approval gate")
    if (
        stage == REQUIREMENTS_STAGE
        and approved
        and (
            task.evidence.requirements_result is None
            or not task.evidence.requirements_result.requirements
        )
    ):
        raise ValueError("approved requirements are missing")
    condition_values = conditions or []
    formal_id = github_record_id
    if gateway is not None:
        status = "APPROVED" if approved else "REJECTED"
        body = (
            f"ai-dev formal decision: {status}\n"
            f"stage: {stage}\ncommit: {commit_sha}\napprover: {approver}\n"
            f"reason: {reason or 'not provided'}\n"
            f"conditions: {', '.join(condition_values) if condition_values else 'none'}"
        )
        formal_id = (
            gateway.add_pull_request_comment(pull_request_number, body)
            if pull_request_number is not None
            else gateway.add_issue_comment(issue_number, body)
        )
    if not formal_id:
        raise ValueError("a successful GitHub comment or review record is required")
    approval = ApprovalRecord(
        issue_number=issue_number,
        pull_request_number=pull_request_number,
        stage=stage,
        commit_sha=commit_sha,
        approver=approver,
        approved=approved,
        reason=reason,
        conditions=condition_values,
        github_record_id=formal_id,
    )
    store.add_approval(approval)
    store.append_event(
        task.task_id,
        approver,
        "approval" if approved else "rejection",
        "recorded",
        {"stage": stage, "commit_sha": commit_sha},
    )
    if stage not in {HUMAN_GATE_STAGE, QA_CONDITIONS_STAGE, REQUIREMENTS_STAGE}:
        return task
    if stage == REQUIREMENTS_STAGE:
        target = (
            WorkflowState.DEPLOYMENT_CONFIGURATION if approved else WorkflowState.REWORK_REQUIRED
        )
        if approved:
            requirements = task.evidence.requirements_result
            assert requirements is not None
            evidence = task.evidence.model_copy(deep=True)
            evidence.requirements_result = requirements.model_copy(update={"human_approved": True})
            task = task.model_copy(update={"evidence": evidence})
    elif stage == QA_CONDITIONS_STAGE:
        target = (
            WorkflowState.HUMAN_APPROVAL_REQUIRED if approved else WorkflowState.REWORK_REQUIRED
        )
    else:
        target = WorkflowState.COMPLETED if approved else WorkflowState.REWORK_REQUIRED
    assert_transition(task.state, target)
    updated = store.save_task(task.model_copy(update={"state": target}))
    store.append_event(
        task.task_id,
        "orchestrator",
        "state_transition",
        "explicit_human_decision",
        {"from": task.state, "to": target, "stage": stage},
    )
    return updated
