"""Deterministic requirement-level traceability validation."""

from __future__ import annotations

from ai_dev_platform.domain.models import RequirementsResult, TraceabilityRecord


def traceability_failure(
    requirements: RequirementsResult | None,
    records: list[TraceabilityRecord],
    *,
    require_optional: bool,
) -> str | None:
    """Return a stable failure reason, or ``None`` when formal traces are complete."""
    if requirements is None or not requirements.requirements:
        return "formal_requirements_missing"
    if not requirements.human_approved:
        return "formal_requirements_not_human_approved"

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
        if not record.implementation_references:
            return "requirement_implementation_reference_missing"
        if not record.review_references:
            return "requirement_review_reference_missing"
        for criterion in requirement.acceptance_criteria:
            if not record.acceptance_criteria_test_references.get(criterion):
                return "acceptance_criterion_test_reference_missing"
    return None


def assert_valid_traceability(
    requirements: RequirementsResult | None,
    records: list[TraceabilityRecord],
    *,
    require_optional: bool,
) -> None:
    """Raise when a trace set is incomplete or references unknown requirements."""
    failure = traceability_failure(requirements, records, require_optional=require_optional)
    if failure is not None:
        raise ValueError(failure)
