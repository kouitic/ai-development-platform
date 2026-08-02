"""Ordered CI quality gates for one exact Pull Request head SHA."""

from __future__ import annotations

import asyncio
from pathlib import Path

from ai_dev_platform.application.quality_artifacts import (
    build_quality_artifact,
    read_quality_artifact,
    write_quality_artifact,
)
from ai_dev_platform.application.requirements import parse_structured_issue_requirements
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import LoadedConfig
from ai_dev_platform.domain.models import (
    Decision,
    EvidenceReference,
    IssueData,
    RequirementsResult,
    ReviewType,
    TaskEvidence,
    TaskRecord,
    TraceabilityRecord,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from ai_dev_platform.infrastructure.github import GitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore, TaskNotFoundError
from ai_dev_platform.providers.base import AgentProvider
from ai_dev_platform.security.scanner import scan_tree


def _bootstrap_evidence(issue: IssueData, verification: VerificationResult) -> TaskEvidence:
    issue_reference = EvidenceReference(
        id="github-issue-requirements",
        kind="github",
        reference="github:issue-body",
        safe_summary="Requirements sourced from the target Issue.",
    )
    requirement_items = parse_structured_issue_requirements(
        issue.body,
        source_reference=issue.url or f"github:issue:{issue.number}",
    )
    requirements = RequirementsResult(
        decision=Decision.PASS,
        summary="Human-authored structured requirements were collected from the target Issue.",
        evidence=[issue_reference],
        requirements=requirement_items,
        business_requirements=[
            item.description for item in requirement_items if item.type == "BUSINESS"
        ],
        acceptance_criteria=[
            criterion for item in requirement_items for criterion in item.acceptance_criteria
        ],
        scope=["Target Issue and Pull Request"],
        requirements_source="STRUCTURED_ISSUE",
        human_approved=True,
    )
    return TaskEvidence(
        requirements_result=requirements,
        trusted_verification_results=[verification],
        traceability=[
            TraceabilityRecord(
                requirement_id=requirement.id,
                implementation_references=[f"commit:{verification.commit_sha}"],
                test_references=[f"verification:{verification.run_id}"],
                acceptance_criteria_test_references={
                    criterion: [f"verification:{verification.run_id}"]
                    for criterion in requirement.acceptance_criteria
                },
            )
            for requirement in requirement_items
        ],
    )


def _assert_verification_target(
    verification: VerificationResult,
    *,
    commit_sha: str,
    changed_files: list[str],
) -> None:
    if verification.overall_status != VerificationStatus.PASS:
        raise ValueError("trusted verification did not pass")
    if verification.commit_sha != commit_sha:
        raise ValueError("trusted verification commit does not match Pull Request head")
    if sorted(verification.changed_files) != sorted(changed_files):
        raise ValueError("trusted verification files do not match Pull Request files")
    if not verification.results or any(
        result.required and result.status.value != "PASS" for result in verification.results
    ):
        raise ValueError("trusted verification contains a failed or missing required result")


def prepare_quality_task(
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    stage: WorkflowState,
    verification: VerificationResult,
) -> TaskRecord:
    """Collect exact targets and accept only trusted, SHA-bound host verification."""
    issue = github.get_issue(issue_number)
    pull_request = github.get_pull_request(pull_request_number)
    if pull_request.base_branch == pull_request.head_branch:
        raise ValueError("Pull Request head and base branches must differ")
    if scan_tree(root):
        raise ValueError("quality gate blocked because secret-like content was detected")
    changed_files = [item.path for item in github.get_changed_files(pull_request_number)]
    _assert_verification_target(
        verification,
        commit_sha=pull_request.head_sha,
        changed_files=changed_files,
    )
    try:
        task = store.get_task_by_issue(issue_number)
    except TaskNotFoundError:
        if stage != WorkflowState.SYSTEM_REVIEW:
            raise ValueError("System Review must create the ordered quality-gate state") from None
        task = store.create_task(
            TaskRecord(
                task_id=f"issue-{issue_number}",
                issue_number=issue_number,
                state=stage,
                commit_sha=pull_request.head_sha,
                branch=pull_request.head_branch,
                pull_request_number=pull_request_number,
                context={
                    "issue_reference": {
                        "number": issue.number,
                        "url": issue.url,
                        "labels": issue.labels,
                    }
                },
                evidence=_bootstrap_evidence(issue, verification),
            )
        )
    else:
        if task.pull_request_number != pull_request_number:
            raise ValueError("persisted quality state belongs to another Pull Request")
        if task.commit_sha != pull_request.head_sha:
            raise ValueError("persisted quality state belongs to another commit SHA")
        if task.state != stage:
            raise ValueError("persisted task is not ready for the requested ordered gate")
        latest = task.evidence.trusted_verification_results[-1]
        _assert_verification_target(
            latest,
            commit_sha=pull_request.head_sha,
            changed_files=changed_files,
        )
    return task


def run_quality_gate(
    loaded: LoadedConfig,
    provider: AgentProvider,
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    stage: WorkflowState,
    verification: VerificationResult,
) -> TaskRecord:
    """Execute one ordered stage and publish its formal comment and unique Check."""
    task = prepare_quality_task(
        store,
        github,
        root,
        issue_number=issue_number,
        pull_request_number=pull_request_number,
        stage=stage,
        verification=verification,
    )
    runner = WorkflowRunner(
        loaded.project,
        loaded.agents,
        provider,
        store,
        root=root,
        github=github,
    )
    return asyncio.run(runner.run_one_stage(task.task_id, stage))


def run_integrated_quality_gates(
    loaded: LoadedConfig,
    provider: AgentProvider,
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    verification: VerificationResult,
    artifact_directory: Path,
) -> TaskRecord:
    """Run System, Business, and QA once each in one process and verify each JSON handoff."""
    stages = (
        (WorkflowState.SYSTEM_REVIEW, ReviewType.SYSTEM, "system-review.json"),
        (WorkflowState.BUSINESS_REVIEW, ReviewType.BUSINESS, "business-review.json"),
        (WorkflowState.QA_ASSESSMENT, ReviewType.QA, "qa-assessment.json"),
    )
    task: TaskRecord | None = None
    for workflow_stage, review_type, filename in stages:
        task = run_quality_gate(
            loaded,
            provider,
            store,
            github,
            root,
            issue_number=issue_number,
            pull_request_number=pull_request_number,
            stage=workflow_stage,
            verification=verification,
        )
        artifact_path = write_quality_artifact(
            artifact_directory / filename,
            build_quality_artifact(task, review_type),
        )
        read_quality_artifact(
            artifact_path,
            expected_stage=review_type,
            issue_number=issue_number,
            pull_request_number=pull_request_number,
            commit_sha=verification.commit_sha or "",
        )
        expected_next = {
            WorkflowState.SYSTEM_REVIEW: WorkflowState.BUSINESS_REVIEW,
            WorkflowState.BUSINESS_REVIEW: WorkflowState.QA_ASSESSMENT,
            WorkflowState.QA_ASSESSMENT: WorkflowState.HUMAN_APPROVAL_REQUIRED,
        }[workflow_stage]
        if task.state != expected_next:
            raise ValueError(f"{workflow_stage.value} did not pass its ordered quality gate")
    assert task is not None
    return task
