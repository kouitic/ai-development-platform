import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_dev_platform.application.traceability import traceability_failure
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    BusinessReviewResult,
    Decision,
    EvidenceReference,
    RequirementItem,
    RequirementsResult,
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
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.providers.mock import MockAgentProvider


def requirement(identifier: str, *, required: bool = True) -> RequirementItem:
    return RequirementItem(
        id=identifier,
        type="BUSINESS" if identifier.startswith("BR") else "FUNCTIONAL",
        description=f"Requirement {identifier}",
        acceptance_criteria=[f"{identifier} criterion one", f"{identifier} criterion two"],
        required=required,
        source_reference="github:issue:90",
    )


def requirements(*items: RequirementItem) -> RequirementsResult:
    return RequirementsResult(
        decision=Decision.PASS,
        summary="human-approved requirements",
        evidence=[EvidenceReference(id="requirements", kind="github", reference="github:issue:90")],
        requirements=list(items),
        requirements_source="STRUCTURED_ISSUE",
        human_approved=True,
    )


def trace(item: RequirementItem) -> TraceabilityRecord:
    return TraceabilityRecord(
        requirement_id=item.id,
        implementation_references=[f"file:src/{item.id.lower()}.py"],
        test_references=[f"test:{item.id}"],
        acceptance_criteria_test_references={
            criterion: [f"test:{item.id}:{index}"]
            for index, criterion in enumerate(item.acceptance_criteria, start=1)
        },
        review_references=[f"review:SYSTEM:{item.id}"],
    )


def trusted_verification() -> VerificationResult:
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
        commit_sha="a" * 40,
    )


def test_qa_does_not_run_when_one_of_three_required_requirements_is_missing(
    initialized_project: Path,
) -> None:
    items = [requirement("BR-001"), requirement("FR-002"), requirement("FR-003")]
    result = requirements(*items)
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "trace-missing.sqlite3")
    common = {
        "decision": Decision.PASS,
        "summary": "reviewed",
        "evidence": [EvidenceReference(id="review", kind="mock", reference="review")],
        "reviewed_commit_sha": "a" * 40,
    }
    task = store.create_task(
        TaskRecord(
            task_id="trace-missing",
            issue_number=90,
            state=WorkflowState.QA_ASSESSMENT,
            commit_sha="a" * 40,
            evidence=TaskEvidence(
                requirements_result=result,
                trusted_verification_results=[trusted_verification()],
                system_reviews=[SystemReviewResult(**common)],
                business_reviews=[
                    BusinessReviewResult(
                        **common, evaluated_requirement_ids=[item.id for item in items]
                    )
                ],
                traceability=[trace(items[0]), trace(items[1])],
            ),
        )
    )
    loaded = load_config(initialized_project)
    provider = MockAgentProvider()
    runner = WorkflowRunner(
        loaded.project, loaded.agents, provider, store, root=initialized_project
    )

    finished = asyncio.run(runner.run_one_stage(task.task_id, WorkflowState.QA_ASSESSMENT))

    assert finished.state == WorkflowState.REWORK_REQUIRED
    assert provider.requests == []


def test_optional_requirement_traceability_obeys_configuration() -> None:
    required_item = requirement("BR-001")
    optional_item = requirement("FR-002", required=False)
    result = requirements(required_item, optional_item)
    records = [trace(required_item)]
    assert traceability_failure(result, records, require_optional=False) is None
    assert (
        traceability_failure(result, records, require_optional=True)
        == "required_requirement_traceability_missing"
    )


def test_unknown_and_duplicate_traceability_requirement_ids_are_rejected() -> None:
    item = requirement("BR-001")
    result = requirements(item)
    unknown = trace(item).model_copy(update={"requirement_id": "BR-999"})
    assert (
        traceability_failure(result, [unknown], require_optional=False)
        == "unknown_traceability_requirement_id"
    )
    assert (
        traceability_failure(result, [trace(item), trace(item)], require_optional=False)
        == "duplicate_traceability_requirement_id"
    )


def test_each_acceptance_criterion_requires_a_test_reference() -> None:
    item = requirement("BR-001")
    incomplete = trace(item).model_copy(
        update={
            "acceptance_criteria_test_references": {
                item.acceptance_criteria[0]: ["test:first-only"]
            }
        }
    )
    assert (
        traceability_failure(requirements(item), [incomplete], require_optional=False)
        == "acceptance_criterion_test_reference_missing"
    )


def test_complete_required_requirement_traceability_is_accepted() -> None:
    items = [requirement("BR-001"), requirement("FR-002"), requirement("FR-003")]
    assert (
        traceability_failure(
            requirements(*items), [trace(item) for item in items], require_optional=False
        )
        is None
    )


def test_duplicate_requirement_ids_are_rejected() -> None:
    duplicate = requirement("BR-001")
    with pytest.raises(ValidationError, match="requirement IDs must be unique"):
        requirements(duplicate, duplicate)
