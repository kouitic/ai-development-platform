import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_dev_platform.application.requirements import requirements_digest
from ai_dev_platform.application.traceability import (
    assert_references_exist_at_commit,
    build_validated_traceability,
    traceability_failure,
)
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    AcceptanceCriterionTestMapping,
    BusinessReviewResult,
    Decision,
    DeveloperResult,
    EvidenceReference,
    ExecutedTestCase,
    RequirementImplementationReference,
    RequirementItem,
    RequirementsApproval,
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


def approval(result: RequirementsResult) -> RequirementsApproval:
    return RequirementsApproval(
        issue_number=90,
        requirements_digest=requirements_digest(result.requirements),
        approved_by="human-reviewer",
        approved_at=datetime.now(UTC),
        github_reference="https://github.example/issues/90#issuecomment-1",
    )


def acceptance_test_case_id(item: RequirementItem, index: int) -> str:
    return f"tests/test_acceptance.py::{item.id.lower()}_{index}"


def trace(item: RequirementItem) -> TraceabilityRecord:
    return TraceabilityRecord(
        requirement_id=item.id,
        design_references=["design:docs/design/traceability.md#要件対応"],
        implementation_references=[f"file:src/{item.id.lower()}.py"],
        acceptance_criteria_test_references={
            criterion: [f"test:{acceptance_test_case_id(item, index)}"]
            for index, criterion in enumerate(item.acceptance_criteria, start=1)
        },
        review_references={
            "SYSTEM": [f"review:SYSTEM:{item.id}"],
            "BUSINESS": [f"review:BUSINESS:{item.id}"],
            "QA": [f"review:QA:{item.id}"],
        },
    )


def trusted_verification(
    *items: RequirementItem,
    status: str = "PASS",
    commit_sha: str = "a" * 40,
) -> VerificationResult:
    now = datetime.now(UTC)
    cases = [
        ExecutedTestCase(
            id=acceptance_test_case_id(item, index),
            node_id=acceptance_test_case_id(item, index),
            file="tests/test_acceptance.py",
            status=status,
            evidence_reference=f"junit:verify:{item.id}:{index}",
        )
        for item in items
        for index, _ in enumerate(item.acceptance_criteria, start=1)
    ]
    return VerificationResult(
        worktree_digest="0" * 64,
        base_commit_sha="b" * 40,
        changed_files=[f"src/{item.id.lower()}.py" for item in items] or ["src/app.py"],
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
        executed_test_cases=cases,
        overall_status=VerificationStatus.PASS,
        started_at=now,
        finished_at=now,
        commit_sha=commit_sha,
    )


def failure(
    root: Path,
    result: RequirementsResult,
    records: list[TraceabilityRecord],
    verification: VerificationResult,
    *,
    require_optional: bool = False,
) -> str | None:
    config = load_config(root).project
    return traceability_failure(
        result,
        approval(result),
        records,
        verification,
        commit_sha="a" * 40,
        config=config,
        require_optional=require_optional,
        issue_number=90,
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
        "evaluated_requirement_ids": [item.id for item in items],
    }
    task = store.create_task(
        TaskRecord(
            task_id="trace-missing",
            issue_number=90,
            state=WorkflowState.QA_ASSESSMENT,
            commit_sha="a" * 40,
            evidence=TaskEvidence(
                requirements_result=result,
                requirements_approval=approval(result),
                trusted_verification_results=[trusted_verification(*items)],
                system_reviews=[SystemReviewResult(**common)],
                business_reviews=[BusinessReviewResult(**common)],
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


def test_verification_success_alone_does_not_complete_traceability(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    result = requirements(item)
    assert (
        failure(
            initialized_project,
            result,
            [TraceabilityRecord(requirement_id=item.id)],
            trusted_verification(item),
        )
        == "requirement_design_reference_missing"
    )


def test_requirement_without_implementation_reference_is_incomplete(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    record = trace(item).model_copy(update={"implementation_references": []})

    assert (
        failure(
            initialized_project,
            requirements(item),
            [record],
            trusted_verification(item),
        )
        == "requirement_implementation_reference_missing"
    )


def test_optional_requirement_traceability_obeys_configuration(
    initialized_project: Path,
) -> None:
    required_item = requirement("BR-001")
    optional_item = requirement("FR-002", required=False)
    result = requirements(required_item, optional_item)
    verification = trusted_verification(required_item, optional_item)
    records = [trace(required_item)]
    assert failure(initialized_project, result, records, verification) is None
    assert (
        failure(
            initialized_project,
            result,
            records,
            verification,
            require_optional=True,
        )
        == "required_requirement_traceability_missing"
    )


def test_unknown_and_duplicate_traceability_requirement_ids_are_rejected(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    result = requirements(item)
    verification = trusted_verification(item)
    unknown = trace(item).model_copy(update={"requirement_id": "BR-999"})
    assert (
        failure(initialized_project, result, [unknown], verification)
        == "unknown_traceability_requirement_id"
    )
    assert (
        failure(initialized_project, result, [trace(item), trace(item)], verification)
        == "duplicate_traceability_requirement_id"
    )


def test_each_acceptance_criterion_requires_an_executed_passed_test(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    incomplete = trace(item).model_copy(
        update={
            "acceptance_criteria_test_references": {
                item.acceptance_criteria[0]: [f"test:{acceptance_test_case_id(item, 1)}"]
            }
        }
    )
    assert (
        failure(
            initialized_project,
            requirements(item),
            [incomplete],
            trusted_verification(item),
        )
        == "acceptance_criterion_test_reference_missing"
    )


@pytest.mark.parametrize("status", ["SKIP", "FAIL", "ERROR"])
def test_non_passed_test_case_cannot_satisfy_acceptance_criterion(
    initialized_project: Path,
    status: str,
) -> None:
    item = requirement("BR-001")
    assert (
        failure(
            initialized_project,
            requirements(item),
            [trace(item)],
            trusted_verification(item, status=status),
        )
        == "acceptance_test_not_passed"
    )


def test_test_results_from_another_commit_are_rejected(initialized_project: Path) -> None:
    item = requirement("BR-001")
    assert (
        failure(
            initialized_project,
            requirements(item),
            [trace(item)],
            trusted_verification(item, commit_sha="c" * 40),
        )
        == "trusted_verification_commit_mismatch"
    )


def test_unexecuted_test_case_cannot_be_used_as_evidence(initialized_project: Path) -> None:
    item = requirement("BR-001")
    verification = trusted_verification(item).model_copy(update={"executed_test_cases": []})

    assert (
        failure(
            initialized_project,
            requirements(item),
            [trace(item)],
            verification,
        )
        == "acceptance_test_not_executed"
    )


def test_complete_required_requirement_traceability_is_accepted(
    initialized_project: Path,
) -> None:
    items = [requirement("BR-001"), requirement("FR-002"), requirement("FR-003")]
    assert (
        failure(
            initialized_project,
            requirements(*items),
            [trace(item) for item in items],
            trusted_verification(*items),
        )
        is None
    )


def test_traceability_rejects_invalid_approval_and_structured_reference_formats(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    result = requirements(item)
    record = trace(item)
    verification = trusted_verification(item)
    config = load_config(initialized_project).project
    common = {
        "commit_sha": "a" * 40,
        "config": config,
        "require_optional": False,
        "issue_number": 90,
    }
    assert (
        traceability_failure(result, None, [record], verification, **common)
        == "formal_requirements_not_human_approved"
    )
    stale_approval = approval(result).model_copy(update={"requirements_digest": "f" * 64})
    assert (
        traceability_failure(result, stale_approval, [record], verification, **common)
        == "requirements_approval_digest_mismatch"
    )
    other_issue_approval = approval(result).model_copy(update={"issue_number": 91})
    assert (
        traceability_failure(result, other_issue_approval, [record], verification, **common)
        == "requirements_approval_digest_mismatch"
    )
    duplicate_cases = verification.model_copy(
        update={"executed_test_cases": [*verification.executed_test_cases] * 2}
    )
    assert (
        traceability_failure(
            result,
            approval(result),
            [record],
            duplicate_cases,
            **common,
        )
        == "duplicate_executed_test_case_id"
    )
    invalid_design = record.model_copy(update={"design_references": ["free-form"]})
    assert (
        failure(initialized_project, result, [invalid_design], verification)
        == "invalid_design_reference"
    )
    invalid_implementation = record.model_copy(
        update={"implementation_references": ["commit:unverified"]}
    )
    assert (
        failure(initialized_project, result, [invalid_implementation], verification)
        == "invalid_implementation_reference"
    )
    unknown_criterion = record.model_copy(
        update={
            "acceptance_criteria_test_references": {
                **record.acceptance_criteria_test_references,
                "unknown criterion": [f"test:{acceptance_test_case_id(item, 1)}"],
            }
        }
    )
    assert (
        failure(initialized_project, result, [unknown_criterion], verification)
        == "unknown_acceptance_criterion_mapping"
    )
    invalid_test = record.model_copy(
        update={
            "acceptance_criteria_test_references": {
                criterion: ["verification:overall-pass"] for criterion in item.acceptance_criteria
            }
        }
    )
    assert (
        failure(initialized_project, result, [invalid_test], verification)
        == "invalid_test_reference"
    )
    missing_review = record.model_copy(update={"review_references": {"QA": ["review:QA:1"]}})
    assert (
        failure(initialized_project, result, [missing_review], verification)
        == "requirement_review_reference_missing"
    )
    invalid_review = record.model_copy(
        update={
            "review_references": {
                **record.review_references,
                "BUSINESS": ["review:SYSTEM:wrong-owner"],
            }
        }
    )
    assert (
        failure(initialized_project, result, [invalid_review], verification)
        == "invalid_review_reference"
    )


def test_nonexistent_implementation_reference_is_rejected(initialized_project: Path) -> None:
    item = requirement("BR-001")
    result = requirements(item)
    developer = DeveloperResult(
        decision=Decision.PASS,
        summary="mapping",
        evidence=[EvidenceReference(id="mapping", kind="mock", reference="mapping")],
        changed_files=["src/missing.py"],
        requirement_implementations=[
            RequirementImplementationReference(
                requirement_id=item.id,
                design_references=["docs/design/traceability.md#要件対応"],
                implementation_references=["src/missing.py"],
            )
        ],
        acceptance_criterion_test_mappings=[
            AcceptanceCriterionTestMapping(
                requirement_id=item.id,
                acceptance_criterion=criterion,
                test_case_ids=[acceptance_test_case_id(item, index)],
            )
            for index, criterion in enumerate(item.acceptance_criteria, start=1)
        ],
    )
    with pytest.raises(ValueError, match="does not exist"):
        build_validated_traceability(
            initialized_project,
            result,
            developer,
            trusted_verification(item).model_copy(update={"changed_files": ["src/missing.py"]}),
            protected_patterns=[],
            protected_path_approved=False,
        )


def test_validated_developer_mappings_create_structured_traceability(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    (initialized_project / "src").mkdir(exist_ok=True)
    (initialized_project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    developer = DeveloperResult(
        decision=Decision.PASS,
        summary="mapping",
        evidence=[EvidenceReference(id="mapping", kind="mock", reference="mapping")],
        changed_files=["src/app.py"],
        requirement_implementations=[
            RequirementImplementationReference(
                requirement_id=item.id,
                design_references=["docs/design/traceability.md#要件対応"],
                implementation_references=["src/app.py"],
            )
        ],
        acceptance_criterion_test_mappings=[
            AcceptanceCriterionTestMapping(
                requirement_id=item.id,
                acceptance_criterion=criterion,
                test_case_ids=[acceptance_test_case_id(item, index)],
            )
            for index, criterion in enumerate(item.acceptance_criteria, start=1)
        ],
    )

    records = build_validated_traceability(
        initialized_project,
        requirements(item),
        developer,
        trusted_verification(item).model_copy(update={"changed_files": ["src/app.py"]}),
        protected_patterns=[".ai-dev/**"],
        protected_path_approved=False,
    )

    assert records[0].design_references == ["design:docs/design/traceability.md#要件対応"]
    assert records[0].implementation_references == ["file:src/app.py"]
    assert records[0].acceptance_criteria_test_references[item.acceptance_criteria[0]] == [
        f"test:{acceptance_test_case_id(item, 1)}"
    ]


def test_repository_escape_and_unapproved_protected_reference_are_rejected(
    initialized_project: Path,
) -> None:
    item = requirement("BR-001")
    verification = trusted_verification(item).model_copy(
        update={"changed_files": [".ai-dev/project.yaml"]}
    )
    base = DeveloperResult(
        decision=Decision.PASS,
        summary="mapping",
        evidence=[EvidenceReference(id="mapping", kind="mock", reference="mapping")],
        changed_files=[".ai-dev/project.yaml"],
        requirement_implementations=[
            RequirementImplementationReference(
                requirement_id=item.id,
                design_references=["../outside.md#section"],
                implementation_references=[".ai-dev/project.yaml"],
            )
        ],
    )
    with pytest.raises(ValueError, match="escapes"):
        build_validated_traceability(
            initialized_project,
            requirements(item),
            base,
            verification,
            protected_patterns=[".ai-dev/**"],
            protected_path_approved=False,
        )

    protected = base.model_copy(
        update={
            "requirement_implementations": [
                RequirementImplementationReference(
                    requirement_id=item.id,
                    design_references=["docs/design/traceability.md#要件対応"],
                    implementation_references=[".ai-dev/project.yaml"],
                )
            ]
        }
    )
    with pytest.raises(ValueError, match="protected path"):
        build_validated_traceability(
            initialized_project,
            requirements(item),
            protected,
            verification,
            protected_patterns=[".ai-dev/**"],
            protected_path_approved=False,
        )


def test_references_must_exist_in_the_target_commit(initialized_project: Path) -> None:
    (initialized_project / "src").mkdir(exist_ok=True)
    (initialized_project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    for arguments in (
        ["init"],
        ["config", "user.email", "tests@example.invalid"],
        ["config", "user.name", "Test User"],
        ["add", "-A"],
        ["commit", "-m", "参照検証用commit"],
    ):
        subprocess.run(
            ["git", *arguments],
            cwd=initialized_project,
            check=True,
            capture_output=True,
        )
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=initialized_project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    valid = TraceabilityRecord(
        requirement_id="BR-001",
        design_references=["design:docs/design/traceability.md#要件対応"],
        implementation_references=["file:src/app.py"],
    )
    assert_references_exist_at_commit(initialized_project, [valid], commit_sha)

    missing = valid.model_copy(update={"implementation_references": ["file:src/missing.py"]})
    with pytest.raises(ValueError, match="target commit"):
        assert_references_exist_at_commit(initialized_project, [missing], commit_sha)


def test_duplicate_requirement_ids_are_rejected() -> None:
    duplicate = requirement("BR-001")
    with pytest.raises(ValidationError, match="requirement IDs must be unique"):
        requirements(duplicate, duplicate)
