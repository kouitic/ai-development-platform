"""Host-validated requirement, implementation, test, and review traceability."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path, PurePosixPath

from ai_dev_platform.application.requirements import requirements_digest
from ai_dev_platform.domain.models import (
    DeveloperResult,
    ProjectConfig,
    RequirementsApproval,
    RequirementsResult,
    ReviewType,
    TraceabilityRecord,
    VerificationResult,
    VerificationStatus,
)


def _repository_path(root: Path, value: str) -> tuple[str, Path]:
    normalized = value.replace("\\", "/")
    relative = PurePosixPath(normalized)
    if (
        not normalized
        or relative.is_absolute()
        or re.match(r"^[A-Za-z]:", normalized)
        or ".." in relative.parts
    ):
        raise ValueError("repository reference escapes the repository root")
    candidate = (root / Path(*relative.parts)).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("repository reference escapes the repository root") from exc
    return relative.as_posix(), candidate


def _is_protected(path: str, protected_patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in protected_patterns)


def _validated_file_reference(
    root: Path,
    reference: str,
    *,
    kind: str,
    changed_files: set[str],
    protected_patterns: list[str],
    protected_path_approved: bool,
    trusted_mock_files: set[str],
) -> str:
    raw = reference.removeprefix(f"{kind}:")
    section = ""
    if kind == "design":
        if "#" not in raw:
            raise ValueError("design reference requires a document section")
        raw, section = raw.split("#", maxsplit=1)
        if not section:
            raise ValueError("design reference section is empty")
    path, candidate = _repository_path(root, raw)
    if kind == "design" and not path.startswith("docs/"):
        raise ValueError("design reference must point to repository documentation")
    if _is_protected(path, protected_patterns) and not protected_path_approved:
        raise ValueError("protected path reference lacks human approval")
    if not candidate.is_file() and path not in trusted_mock_files:
        raise ValueError("referenced repository file does not exist")
    if kind == "file" and path not in changed_files:
        raise ValueError("implementation reference is not part of the verified change")
    return f"{kind}:{path}{f'#{section}' if section else ''}"


def build_validated_traceability(
    root: Path,
    requirements: RequirementsResult,
    developer: DeveloperResult,
    verification: VerificationResult,
    *,
    protected_patterns: list[str],
    protected_path_approved: bool,
    trusted_mock_files: set[str] | None = None,
) -> list[TraceabilityRecord]:
    """Build traces only from validated Agent mappings and host JUnit results."""
    requirement_by_id = {item.id: item for item in requirements.requirements}
    changed_files = {Path(value).as_posix() for value in verification.changed_files}
    trusted_mock_files = trusted_mock_files or set()
    test_cases = {item.id: item for item in verification.executed_test_cases}
    if len(test_cases) != len(verification.executed_test_cases):
        raise ValueError("duplicate executed test case ID")

    traces = {
        requirement.id: TraceabilityRecord(requirement_id=requirement.id)
        for requirement in requirements.requirements
    }
    implementation_ids = [item.requirement_id for item in developer.requirement_implementations]
    if len(implementation_ids) != len(set(implementation_ids)):
        raise ValueError("duplicate requirement implementation mapping")
    if not set(implementation_ids).issubset(requirement_by_id):
        raise ValueError("implementation mapping contains an unknown requirement ID")
    for implementation_mapping in developer.requirement_implementations:
        design_references = [
            _validated_file_reference(
                root,
                reference,
                kind="design",
                changed_files=changed_files,
                protected_patterns=protected_patterns,
                protected_path_approved=protected_path_approved,
                trusted_mock_files=trusted_mock_files,
            )
            for reference in implementation_mapping.design_references
        ]
        implementation_references = [
            _validated_file_reference(
                root,
                reference,
                kind="file",
                changed_files=changed_files,
                protected_patterns=protected_patterns,
                protected_path_approved=protected_path_approved,
                trusted_mock_files=trusted_mock_files,
            )
            for reference in implementation_mapping.implementation_references
        ]
        traces[implementation_mapping.requirement_id] = traces[
            implementation_mapping.requirement_id
        ].model_copy(
            update={
                "design_references": list(dict.fromkeys(design_references)),
                "implementation_references": list(dict.fromkeys(implementation_references)),
            }
        )

    if verification.overall_status != VerificationStatus.PASS:
        raise ValueError("test mappings require a passed host verification")

    mapping_keys = [
        (item.requirement_id, item.acceptance_criterion)
        for item in developer.acceptance_criterion_test_mappings
    ]
    if len(mapping_keys) != len(set(mapping_keys)):
        raise ValueError("duplicate acceptance criterion test mapping")
    for criterion_mapping in developer.acceptance_criterion_test_mappings:
        requirement = requirement_by_id.get(criterion_mapping.requirement_id)
        if requirement is None:
            raise ValueError("test mapping contains an unknown requirement ID")
        if criterion_mapping.acceptance_criterion not in requirement.acceptance_criteria:
            raise ValueError("test mapping criterion does not exactly match the requirement")
        references: list[str] = []
        for test_case_id in criterion_mapping.test_case_ids:
            test_case = test_cases.get(test_case_id)
            if test_case is None:
                raise ValueError("test mapping refers to an unexecuted test case")
            if test_case.status != "PASS":
                raise ValueError("only a passed test case can satisfy an acceptance criterion")
            references.append(f"test:{test_case_id}")
        record = traces[criterion_mapping.requirement_id]
        acceptance = dict(record.acceptance_criteria_test_references)
        acceptance[criterion_mapping.acceptance_criterion] = list(dict.fromkeys(references))
        traces[criterion_mapping.requirement_id] = record.model_copy(
            update={"acceptance_criteria_test_references": acceptance}
        )
    return [traces[item.id] for item in requirements.requirements]


def assert_references_exist_at_commit(
    root: Path,
    records: list[TraceabilityRecord],
    commit_sha: str,
) -> None:
    """Require every referenced repository file to exist in the committed tree."""
    if not (root / ".git").exists():
        return
    references = [
        *(
            reference.removeprefix("design:").split("#", maxsplit=1)[0]
            for record in records
            for reference in record.design_references
        ),
        *(
            reference.removeprefix("file:")
            for record in records
            for reference in record.implementation_references
        ),
    ]
    for reference in references:
        try:
            result = subprocess.run(
                [
                    "git",
                    "-c",
                    f"safe.directory={root.resolve().as_posix()}",
                    "cat-file",
                    "-e",
                    f"{commit_sha}:{reference}",
                ],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError("commit reference validation failed") from exc
        if result.returncode != 0:
            raise ValueError("referenced file does not exist at the target commit")


def review_coverage_failure(
    requirements: RequirementsResult,
    review_type: ReviewType,
    evaluated_requirement_ids: list[str],
    excluded_requirement_reasons: dict[str, str],
    config: ProjectConfig,
) -> str | None:
    """Validate one review's explicit requirement scope against project policy."""
    formal_ids = {item.id for item in requirements.requirements}
    evaluated_ids = set(evaluated_requirement_ids)
    excluded_ids = set(excluded_requirement_reasons)
    if len(evaluated_requirement_ids) != len(evaluated_ids):
        return "duplicate_review_requirement_id"
    if not evaluated_ids.issubset(formal_ids) or not excluded_ids.issubset(formal_ids):
        return "unknown_review_requirement_id"
    if evaluated_ids & excluded_ids:
        return "review_requirement_both_evaluated_and_excluded"
    for requirement in requirements.requirements:
        required_reviews = config.review_coverage[requirement.type].required_reviews
        if review_type in required_reviews and requirement.id not in evaluated_ids:
            return "required_review_requirement_missing"
        if (
            requirement.id not in evaluated_ids
            and not excluded_requirement_reasons.get(requirement.id, "").strip()
        ):
            return "review_exclusion_reason_missing"
    return None


def traceability_failure(
    requirements: RequirementsResult | None,
    approval: RequirementsApproval | None,
    records: list[TraceabilityRecord],
    verification: VerificationResult | None,
    *,
    commit_sha: str,
    config: ProjectConfig,
    require_optional: bool,
    issue_number: int | None = None,
    review_scope: set[ReviewType] | None = None,
) -> str | None:
    """Return a stable failure reason when host-verifiable traces are incomplete."""
    if requirements is None or not requirements.requirements:
        return "formal_requirements_missing"
    if approval is None or not requirements.human_approved:
        return "formal_requirements_not_human_approved"
    if (
        approval.issue_number < 1
        or (issue_number is not None and approval.issue_number != issue_number)
        or approval.requirements_digest != requirements_digest(requirements.requirements)
    ):
        return "requirements_approval_digest_mismatch"
    if (
        verification is None
        or verification.overall_status != VerificationStatus.PASS
        or verification.commit_sha != commit_sha
    ):
        return "trusted_verification_commit_mismatch"
    test_cases = {item.id: item for item in verification.executed_test_cases}
    if len(test_cases) != len(verification.executed_test_cases):
        return "duplicate_executed_test_case_id"

    requirement_ids = [item.id for item in requirements.requirements]
    if len(requirement_ids) != len(set(requirement_ids)):
        return "duplicate_requirement_id"
    record_ids = [record.requirement_id for record in records]
    if len(record_ids) != len(set(record_ids)):
        return "duplicate_traceability_requirement_id"
    if not set(record_ids).issubset(requirement_ids):
        return "unknown_traceability_requirement_id"

    by_id = {record.requirement_id: record for record in records}
    for requirement in requirements.requirements:
        if not requirement.required and not require_optional:
            continue
        record = by_id.get(requirement.id)
        if record is None:
            return "required_requirement_traceability_missing"
        if not record.design_references:
            return "requirement_design_reference_missing"
        if not record.implementation_references:
            return "requirement_implementation_reference_missing"
        if any(not value.startswith("design:") for value in record.design_references):
            return "invalid_design_reference"
        if any(not value.startswith("file:") for value in record.implementation_references):
            return "invalid_implementation_reference"
        if not set(record.acceptance_criteria_test_references).issubset(
            requirement.acceptance_criteria
        ):
            return "unknown_acceptance_criterion_mapping"
        for criterion in requirement.acceptance_criteria:
            references = record.acceptance_criteria_test_references.get(criterion, [])
            if not references:
                return "acceptance_criterion_test_reference_missing"
            for reference in references:
                if not reference.startswith("test:"):
                    return "invalid_test_reference"
                test_case = test_cases.get(reference.removeprefix("test:"))
                if test_case is None:
                    return "acceptance_test_not_executed"
                if test_case.status != "PASS":
                    return "acceptance_test_not_passed"
        required_reviews = config.review_coverage[requirement.type].required_reviews
        if review_scope is not None:
            required_reviews = [item for item in required_reviews if item in review_scope]
        for review_type in required_reviews:
            references = record.review_references.get(review_type, [])
            if not references:
                return "requirement_review_reference_missing"
            if any(
                not reference.startswith(f"review:{review_type.value}:") for reference in references
            ):
                return "invalid_review_reference"
    return None


def assert_valid_traceability(
    requirements: RequirementsResult | None,
    approval: RequirementsApproval | None,
    records: list[TraceabilityRecord],
    verification: VerificationResult | None,
    *,
    commit_sha: str,
    config: ProjectConfig,
    require_optional: bool,
    issue_number: int | None = None,
) -> None:
    """Raise when a trace set contains missing or unverifiable formal evidence."""
    failure = traceability_failure(
        requirements,
        approval,
        records,
        verification,
        commit_sha=commit_sha,
        config=config,
        require_optional=require_optional,
        issue_number=issue_number,
    )
    if failure is not None:
        raise ValueError(failure)
