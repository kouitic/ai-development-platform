"""Deterministic workflow transitions independent of LLM instructions."""

from __future__ import annotations

from ai_dev_platform.domain.models import WorkflowState

ALLOWED_TRANSITIONS: dict[WorkflowState, frozenset[WorkflowState]] = {
    WorkflowState.NEW: frozenset({WorkflowState.REQUIREMENTS_ANALYSIS}),
    WorkflowState.REQUIREMENTS_ANALYSIS: frozenset(
        {
            WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED,
            WorkflowState.DEPLOYMENT_CONFIGURATION,
            WorkflowState.FAILED,
        }
    ),
    WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED: frozenset(
        {WorkflowState.DEPLOYMENT_CONFIGURATION, WorkflowState.REWORK_REQUIRED}
    ),
    WorkflowState.DEPLOYMENT_CONFIGURATION: frozenset(
        {
            WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED,
            WorkflowState.PLANNING,
            WorkflowState.FAILED,
        }
    ),
    WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED: frozenset(
        {WorkflowState.DEPLOYMENT_CONFIGURATION}
    ),
    WorkflowState.PLANNING: frozenset({WorkflowState.DESIGNING, WorkflowState.FAILED}),
    WorkflowState.DESIGNING: frozenset({WorkflowState.IMPLEMENTING, WorkflowState.FAILED}),
    WorkflowState.IMPLEMENTING: frozenset({WorkflowState.AUTOMATED_TESTING, WorkflowState.FAILED}),
    WorkflowState.AUTOMATED_TESTING: frozenset(
        {WorkflowState.SYSTEM_REVIEW, WorkflowState.REWORK_REQUIRED, WorkflowState.FAILED}
    ),
    WorkflowState.SYSTEM_REVIEW: frozenset(
        {WorkflowState.BUSINESS_REVIEW, WorkflowState.REWORK_REQUIRED, WorkflowState.FAILED}
    ),
    WorkflowState.BUSINESS_REVIEW: frozenset(
        {WorkflowState.QA_ASSESSMENT, WorkflowState.REWORK_REQUIRED, WorkflowState.FAILED}
    ),
    WorkflowState.QA_ASSESSMENT: frozenset(
        {
            WorkflowState.HUMAN_APPROVAL_REQUIRED,
            WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED,
            WorkflowState.REWORK_REQUIRED,
            WorkflowState.BLOCKED,
            WorkflowState.FAILED,
        }
    ),
    WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED: frozenset(
        {WorkflowState.HUMAN_APPROVAL_REQUIRED, WorkflowState.REWORK_REQUIRED}
    ),
    WorkflowState.REWORK_REQUIRED: frozenset({WorkflowState.IMPLEMENTING, WorkflowState.BLOCKED}),
    WorkflowState.HUMAN_APPROVAL_REQUIRED: frozenset(
        {WorkflowState.COMPLETED, WorkflowState.REWORK_REQUIRED}
    ),
    WorkflowState.PAUSED: frozenset(state for state in WorkflowState if state != WorkflowState.NEW),
    WorkflowState.BLOCKED: frozenset(),
    WorkflowState.COMPLETED: frozenset(),
    WorkflowState.FAILED: frozenset(),
    WorkflowState.SECURITY_INCIDENT_REQUIRES_HUMAN: frozenset(),
    WorkflowState.DATA_EXPOSURE_REQUIRES_HUMAN: frozenset(),
}

TERMINAL_STATES = frozenset(
    {
        WorkflowState.HUMAN_APPROVAL_REQUIRED,
        WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED,
        WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED,
        WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED,
        WorkflowState.PAUSED,
        WorkflowState.BLOCKED,
        WorkflowState.COMPLETED,
        WorkflowState.FAILED,
        WorkflowState.SECURITY_INCIDENT_REQUIRES_HUMAN,
        WorkflowState.DATA_EXPOSURE_REQUIRES_HUMAN,
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a transition is not part of the reviewed state machine."""


def assert_transition(current: WorkflowState, target: WorkflowState) -> None:
    """Reject transitions outside the deterministic transition table."""
    if target in {
        WorkflowState.PAUSED,
        WorkflowState.BLOCKED,
        WorkflowState.SECURITY_INCIDENT_REQUIRES_HUMAN,
        WorkflowState.DATA_EXPOSURE_REQUIRES_HUMAN,
    }:
        return
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"transition is not allowed: {current} -> {target}")
