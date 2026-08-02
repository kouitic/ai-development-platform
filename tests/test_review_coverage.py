from pathlib import Path

import pytest

from ai_dev_platform.application.traceability import review_coverage_failure
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    Decision,
    EvidenceReference,
    RequirementItem,
    RequirementsResult,
    ReviewType,
)


def formal_requirements() -> RequirementsResult:
    requirement_types = [
        ("BR-001", "BUSINESS"),
        ("FR-001", "FUNCTIONAL"),
        ("NFR-001", "NON_FUNCTIONAL"),
        ("SEC-001", "SECURITY"),
        ("OPS-001", "OPERATIONAL"),
    ]
    return RequirementsResult(
        decision=Decision.PASS,
        summary="formal requirements",
        evidence=[EvidenceReference(id="issue", kind="github", reference="github:issue:1")],
        requirements=[
            RequirementItem(
                id=identifier,
                type=requirement_type,
                description=identifier,
                acceptance_criteria=[f"{identifier} is verified"],
                source_reference="github:issue:1",
            )
            for identifier, requirement_type in requirement_types
        ],
        requirements_source="STRUCTURED_ISSUE",
        human_approved=True,
    )


@pytest.mark.parametrize(
    ("review_type", "evaluated", "excluded"),
    [
        (
            ReviewType.SYSTEM,
            ["FR-001", "NFR-001", "SEC-001", "OPS-001"],
            {"BR-001": "System Review対象外の業務要件"},
        ),
        (
            ReviewType.BUSINESS,
            ["BR-001", "FR-001"],
            {
                "NFR-001": "Business Review対象外の非機能要件",
                "SEC-001": "Business Review対象外のセキュリティ要件",
                "OPS-001": "Business Review対象外の運用要件",
            },
        ),
        (
            ReviewType.QA,
            ["BR-001", "FR-001", "NFR-001", "SEC-001", "OPS-001"],
            {},
        ),
    ],
)
def test_requirement_type_review_policy_accepts_complete_scope(
    initialized_project: Path,
    review_type: ReviewType,
    evaluated: list[str],
    excluded: dict[str, str],
) -> None:
    config = load_config(initialized_project).project

    assert (
        review_coverage_failure(formal_requirements(), review_type, evaluated, excluded, config)
        is None
    )


@pytest.mark.parametrize(
    ("review_type", "evaluated", "excluded"),
    [
        (
            ReviewType.SYSTEM,
            ["FR-001", "NFR-001", "OPS-001"],
            {"BR-001": "System Review対象外"},
        ),
        (
            ReviewType.BUSINESS,
            ["BR-001"],
            {
                "NFR-001": "Business Review対象外",
                "SEC-001": "Business Review対象外",
                "OPS-001": "Business Review対象外",
            },
        ),
        (ReviewType.QA, ["BR-001", "FR-001", "NFR-001", "SEC-001"], {}),
    ],
)
def test_required_review_scope_cannot_omit_a_requirement(
    initialized_project: Path,
    review_type: ReviewType,
    evaluated: list[str],
    excluded: dict[str, str],
) -> None:
    config = load_config(initialized_project).project

    assert (
        review_coverage_failure(formal_requirements(), review_type, evaluated, excluded, config)
        == "required_review_requirement_missing"
    )


def test_review_cannot_evaluate_an_unknown_requirement(initialized_project: Path) -> None:
    config = load_config(initialized_project).project

    assert (
        review_coverage_failure(
            formal_requirements(),
            ReviewType.SYSTEM,
            ["FR-001", "NFR-001", "SEC-001", "OPS-001", "UNKNOWN-001"],
            {"BR-001": "対象外"},
            config,
        )
        == "unknown_review_requirement_id"
    )
