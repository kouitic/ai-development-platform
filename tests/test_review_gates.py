import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    AgentResult,
    AgentRunStatus,
    BusinessReviewResult,
    Decision,
    DeveloperResult,
    EvidenceReference,
    Finding,
    FindingSeverity,
    FindingStatus,
    QaAssessmentResult,
    RequirementItem,
    RequirementsResult,
    ReviewCondition,
    ReviewType,
    SystemReviewResult,
    TaskEvidence,
    TaskRecord,
    TraceabilityRecord,
    VerificationCommandResult,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from ai_dev_platform.domain.models import TestStatus as RunStatus
from ai_dev_platform.infrastructure.github import GitHubError, MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.providers.mock import MockAgentProvider


def reference() -> EvidenceReference:
    return EvidenceReference(id="review-evidence", kind="mock", reference="mock-review")


def trusted_verification(commit_sha: str = "a" * 40) -> VerificationResult:
    now = datetime.now(UTC)
    return VerificationResult(
        worktree_digest="0" * 64,
        base_commit_sha="b" * 40,
        changed_files=["src/app.py"],
        commands=[["mock", "verify"]],
        results=[
            VerificationCommandResult(
                name="required",
                argv=["mock", "verify"],
                status=RunStatus.PASS,
                exit_code=0,
                evidence_reference="verification:required",
            )
        ],
        overall_status=VerificationStatus.PASS,
        started_at=now,
        finished_at=now,
        commit_sha=commit_sha,
    )


def finding(severity: FindingSeverity = FindingSeverity.MAJOR) -> Finding:
    return Finding(
        id="SYS-001",
        severity=severity,
        category="security",
        requirement_ids=["BR-001"],
        file="src/app.py",
        line=1,
        problem="unsafe behavior",
        evidence=["review-evidence"],
        business_impact="data could be exposed",
        required_fix="enforce the guard",
        acceptance_test="run the security regression test",
        blocking=severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR},
    )


def system_output(
    *,
    decision: Decision = Decision.PASS,
    findings: list[Finding] | None = None,
    conditions: list[ReviewCondition] | None = None,
) -> dict[str, object]:
    return SystemReviewResult(
        decision=decision,
        summary="reviewed",
        evidence=[reference()],
        findings=findings or [],
        conditions=conditions or [],
        reviewed_commit_sha="a" * 40,
        reviewed_files=["src/app.py"],
    ).model_dump(mode="json")


def review_runner(
    root: Path,
    output: dict[str, object],
    *,
    issue: int,
    config_minor_blocks: bool = False,
    github: MockGitHubGateway | None = None,
) -> tuple[WorkflowRunner, SQLiteStateStore, TaskRecord, MockAgentProvider]:
    loaded = load_config(root)
    project = loaded.project.model_copy(
        update={
            "workflow": loaded.project.workflow.model_copy(
                update={"minor_findings_block": config_minor_blocks}
            )
        }
    )
    store = SQLiteStateStore(root / ".ai-dev" / "local" / f"review-{issue}.sqlite3")
    task = store.create_task(
        TaskRecord(
            task_id=f"issue-{issue}",
            issue_number=issue,
            state=WorkflowState.SYSTEM_REVIEW,
            commit_sha="a" * 40,
            branch=f"ai/issue-{issue}-review",
            pull_request_number=1 if github is not None else None,
            evidence=TaskEvidence(
                trusted_verification_results=[trusted_verification()],
                traceability=[TraceabilityRecord(requirement_id="BR-001")],
            ),
            context={
                "security_scan_results": ["passed"],
                "static_analysis_results": ["passed"],
                "dependency_scan_results": ["passed"],
            },
        )
    )
    provider = MockAgentProvider(
        [AgentResult(status=AgentRunStatus.SUCCESS, model="mock", output=output)]
    )
    runner = WorkflowRunner(
        project,
        loaded.agents,
        provider,
        store,
        root=root,
        github=github,
    )
    return runner, store, task, provider


@pytest.mark.parametrize("severity", [FindingSeverity.CRITICAL, FindingSeverity.MAJOR])
def test_critical_and_major_findings_always_rework(
    initialized_project: Path, severity: FindingSeverity
) -> None:
    runner, _, task, _ = review_runner(
        initialized_project,
        system_output(decision=Decision.PASS, findings=[finding(severity)]),
        issue=10 if severity == FindingSeverity.CRITICAL else 11,
    )
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW))
    assert finished.state == WorkflowState.REWORK_REQUIRED
    assert finished.evidence.unresolved_findings[0].id == "SYS-001"


def test_minor_finding_obeys_project_policy(initialized_project: Path) -> None:
    output = system_output(findings=[finding(FindingSeverity.MINOR)])
    allowed, _, task, _ = review_runner(initialized_project, output, issue=12)
    assert (
        asyncio.run(allowed.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW)).state
        == WorkflowState.BUSINESS_REVIEW
    )
    blocked, _, task2, _ = review_runner(
        initialized_project, output, issue=13, config_minor_blocks=True
    )
    assert (
        asyncio.run(blocked.run_one_stage(task2.task_id, WorkflowState.SYSTEM_REVIEW)).state
        == WorkflowState.REWORK_REQUIRED
    )


def test_pass_with_conditions_is_not_unconditional_pass(initialized_project: Path) -> None:
    condition = ReviewCondition(
        condition="Confirm migration window",
        owner="operator",
        due_or_next_stage="before QA",
        human_approval_required=True,
        unmet_action="REWORK",
    )
    runner, _, task, _ = review_runner(
        initialized_project,
        system_output(decision=Decision.PASS_WITH_CONDITIONS, conditions=[condition]),
        issue=14,
    )
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW))
    assert finished.state == WorkflowState.REWORK_REQUIRED


def test_unknown_evidence_reference_is_rejected(initialized_project: Path) -> None:
    broken = finding().model_copy(update={"evidence": ["unknown-evidence"]})
    runner, _, task, _ = review_runner(
        initialized_project, system_output(findings=[broken]), issue=15
    )
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW))
    assert finished.state == WorkflowState.REWORK_REQUIRED


def test_addressed_finding_remains_until_a_clean_rereview(initialized_project: Path) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=16)
    task = task.model_copy(
        update={
            "evidence": task.evidence.model_copy(
                update={
                    "unresolved_findings": [
                        finding().model_copy(
                            update={
                                "origin_review_type": ReviewType.SYSTEM,
                                "origin_review_run_id": "REVIEW-original",
                                "origin_commit_sha": "a" * 40,
                            }
                        )
                    ]
                },
                deep=True,
            )
        }
    )
    developer = DeveloperResult(
        decision=Decision.PASS,
        summary="fixed",
        evidence=[reference()],
        addressed_finding_ids=["SYS-001"],
        changed_files=["src/app.py"],
        change_summary="added guard",
    )
    pending = runner._persist_result(task, developer)
    assert pending.evidence.unresolved_findings[0].status == FindingStatus.FIX_CLAIMED
    runner._bind_findings_to_verified_commit(pending.evidence, "a" * 40)
    clean_review = SystemReviewResult.model_validate(
        {
            **system_output(),
            "resolved_finding_ids": ["SYS-001"],
            "finding_resolution_evidence": {"SYS-001": ["review-evidence"]},
        }
    )
    resolved = runner._persist_result(pending, clean_review)
    assert resolved.evidence.unresolved_findings[0].status == FindingStatus.RESOLVED


def test_qa_conditions_use_dedicated_human_wait(initialized_project: Path) -> None:
    loaded = load_config(initialized_project)
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "qa-condition.sqlite3")
    common = {
        "decision": Decision.PASS,
        "summary": "passed",
        "evidence": [reference()],
    }
    evidence = TaskEvidence(
        requirements_result=RequirementsResult(
            decision=Decision.PASS,
            summary="approved requirements",
            evidence=[reference()],
            requirements=[
                RequirementItem(
                    id="BR-001",
                    type="BUSINESS",
                    description="Required behavior",
                    acceptance_criteria=["Required behavior is verified"],
                    source_reference="github:issue:17",
                )
            ],
            requirements_source="STRUCTURED_ISSUE",
            human_approved=True,
        ),
        trusted_verification_results=[trusted_verification()],
        system_reviews=[
            SystemReviewResult(
                **common,
                reviewed_commit_sha="a" * 40,
                reviewed_files=["src/app.py"],
            )
        ],
        business_reviews=[
            BusinessReviewResult(
                **common,
                evaluated_requirement_ids=["BR-001"],
                reviewed_commit_sha="a" * 40,
            )
        ],
        traceability=[
            TraceabilityRecord(
                requirement_id="BR-001",
                implementation_references=["commit:" + "a" * 40],
                test_references=["verification:test"],
                acceptance_criteria_test_references={
                    "Required behavior is verified": ["verification:test"]
                },
                review_references=["review:SYSTEM:test"],
            )
        ],
    )
    task = store.create_task(
        TaskRecord(
            task_id="issue-17",
            issue_number=17,
            state=WorkflowState.QA_ASSESSMENT,
            commit_sha="a" * 40,
            evidence=evidence,
            context={
                "security_scan_results": ["passed"],
                "static_analysis_results": ["passed"],
                "dependency_scan_results": ["passed"],
            },
        )
    )
    qa_result = QaAssessmentResult(
        decision=Decision.PASS_WITH_CONDITIONS,
        summary="conditional",
        evidence=[reference()],
        conditions=[
            ReviewCondition(
                condition="human confirms risk",
                owner="human",
                due_or_next_stage="before final approval",
                human_approval_required=True,
                unmet_action="BLOCK",
            )
        ],
        reviewed_evidence_ids=["review-evidence"],
    )
    provider = MockAgentProvider(
        [
            AgentResult(
                status=AgentRunStatus.SUCCESS,
                model="mock",
                output=qa_result.model_dump(mode="json"),
            )
        ]
    )
    runner = WorkflowRunner(
        loaded.project, loaded.agents, provider, store, root=initialized_project
    )
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.QA_ASSESSMENT))
    assert finished.state == WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED


class FailingCheckGateway(MockGitHubGateway):
    def set_check_result(self, name: str, commit_sha: str, conclusion: str, summary: str) -> None:
        del name, commit_sha, conclusion, summary
        raise GitHubError("simulated check failure")


def test_github_check_failure_does_not_advance_review(initialized_project: Path) -> None:
    github = FailingCheckGateway()
    github.issues[18] = {"title": "Issue", "body": "BR-001", "labels": []}
    github.pull_requests[1] = {
        "number": 1,
        "title": "PR",
        "body": "",
        "head_branch": "ai/issue-18-review",
        "base_branch": "main",
        "head_sha": "a" * 40,
        "url": "mock://pulls/1",
    }
    runner, _, task, _ = review_runner(
        initialized_project, system_output(), issue=18, github=github
    )
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW))
    assert finished.state == WorkflowState.FAILED
    assert finished.state != WorkflowState.BUSINESS_REVIEW


def test_only_origin_review_can_resolve_with_acceptance_evidence(
    initialized_project: Path,
) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=19)
    tracked = finding().model_copy(
        update={
            "origin_review_type": ReviewType.SYSTEM,
            "origin_review_run_id": "REVIEW-origin",
            "origin_commit_sha": task.commit_sha,
            "status": FindingStatus.VERIFICATION_REQUIRED,
            "resolution_candidate_commit_sha": task.commit_sha,
        }
    )
    task = task.model_copy(
        update={
            "evidence": task.evidence.model_copy(
                update={"unresolved_findings": [tracked]}, deep=True
            )
        }
    )
    business = BusinessReviewResult(
        decision=Decision.PASS,
        summary="claims resolution",
        evidence=[reference()],
        resolved_finding_ids=["SYS-001"],
        finding_resolution_evidence={"SYS-001": ["review-evidence"]},
        reviewed_commit_sha=task.commit_sha,
    )
    with pytest.raises(ValueError, match="owner, SHA, or acceptance evidence"):
        runner._persist_result(task, business)

    no_evidence = SystemReviewResult(
        decision=Decision.PASS,
        summary="missing acceptance evidence",
        evidence=[reference()],
        resolved_finding_ids=["SYS-001"],
        finding_resolution_evidence={"SYS-001": []},
        reviewed_commit_sha=task.commit_sha,
    )
    with pytest.raises(ValueError, match="acceptance evidence"):
        runner._persist_result(task, no_evidence)


def test_finding_reopens_and_sha_change_requires_reverification(
    initialized_project: Path,
) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=20)
    resolved = finding().model_copy(
        update={
            "origin_review_type": ReviewType.SYSTEM,
            "origin_review_run_id": "REVIEW-origin",
            "origin_commit_sha": task.commit_sha,
            "status": FindingStatus.RESOLVED,
            "resolved_by_review_run_id": "REVIEW-resolved",
            "resolved_at_commit_sha": task.commit_sha,
            "resolution_evidence": ["review-evidence"],
        }
    )
    task = task.model_copy(
        update={
            "evidence": task.evidence.model_copy(
                update={"unresolved_findings": [resolved]}, deep=True
            )
        }
    )
    recurring = SystemReviewResult.model_validate(system_output(findings=[finding()]))
    reopened = runner._persist_result(task, recurring)
    assert reopened.evidence.unresolved_findings[0].status == FindingStatus.REOPENED
    assert reopened.evidence.unresolved_findings[0].recurrence

    task.evidence.unresolved_findings = [resolved]
    runner._bind_findings_to_verified_commit(task.evidence, "d" * 40)
    stale = task.evidence.unresolved_findings[0]
    assert stale.status == FindingStatus.VERIFICATION_REQUIRED
    assert stale.resolution_candidate_commit_sha == "d" * 40


def test_one_system_review_run_is_persisted_once(initialized_project: Path) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=21)
    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.SYSTEM_REVIEW))
    assert len(finished.evidence.system_reviews) == 1


def test_system_review_run_id_is_an_idempotency_key(initialized_project: Path) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=22)
    result = SystemReviewResult.model_validate(
        {**system_output(), "run_id": "REVIEW-idempotent-system"}
    )
    first = runner._persist_result(task, result)
    second = runner._persist_result(first, result)
    assert [review.run_id for review in second.evidence.system_reviews] == [result.run_id]


def test_qa_context_deduplicates_legacy_system_review_runs(initialized_project: Path) -> None:
    runner, _, task, _ = review_runner(initialized_project, system_output(), issue=23)
    result = SystemReviewResult.model_validate(
        {**system_output(), "run_id": "REVIEW-legacy-duplicate"}
    )
    duplicate_evidence = task.evidence.model_copy(
        update={"system_reviews": [result, result]}, deep=True
    )
    qa_task = task.model_copy(
        update={"state": WorkflowState.QA_ASSESSMENT, "evidence": duplicate_evidence}
    )
    context = runner.context_builder.build(
        qa_task, WorkflowState.QA_ASSESSMENT, runner.agents["qa"]
    )
    system_reviews = context["all_quality_evidence"]["system_reviews"]
    assert [review["run_id"] for review in system_reviews] == [result.run_id]
