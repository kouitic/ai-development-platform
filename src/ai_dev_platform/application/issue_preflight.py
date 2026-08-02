"""Fail-closed validation for manually dispatched, human-approved Issues."""

from __future__ import annotations

from dataclasses import dataclass

from ai_dev_platform.application.deployment import (
    build_deployment_configuration,
    deployment_digest,
    find_deployment_approval,
    parse_structured_deployment_answers,
)
from ai_dev_platform.application.requirements import (
    find_requirements_approval,
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.domain.models import (
    DeploymentConfiguration,
    IssueData,
    RequirementItem,
    RequirementsApproval,
)
from ai_dev_platform.infrastructure.github import GitHubGateway

APPROVED_ISSUE_LABEL = "ai:approved"


@dataclass(frozen=True, slots=True)
class ApprovedIssueEvidence:
    """Validated Issue inputs safe to seed into one ephemeral workflow run."""

    issue: IssueData
    requirements: list[RequirementItem]
    requirements_approval: RequirementsApproval
    deployment_configuration: DeploymentConfiguration
    deployment_digest: str


def validate_approved_issue(gateway: GitHubGateway, issue_number: int) -> ApprovedIssueEvidence:
    """Require current digests, two human approvals, and the approval label."""
    issue = gateway.get_issue(issue_number)
    if APPROVED_ISSUE_LABEL not in issue.labels:
        raise ValueError(f"Issue requires the {APPROVED_ISSUE_LABEL} label")
    source_reference = issue.url or f"github:issue:{issue.number}"
    requirements = parse_structured_issue_requirements(
        issue.body, source_reference=source_reference
    )
    comments = gateway.get_issue_comments(issue.number)
    requirements_approval = find_requirements_approval(issue.number, requirements, comments)
    if requirements_approval is None:
        raise ValueError("formal requirements approval is missing, rejected, or stale")
    answers = parse_structured_deployment_answers(issue.body)
    deployment_approval = find_deployment_approval(answers, comments)
    if deployment_approval is None:
        raise ValueError("deployment approval is missing, rejected, or stale")
    configuration = build_deployment_configuration(
        answers,
        approver=deployment_approval.author,
        github_reference=deployment_approval.url,
    )
    return ApprovedIssueEvidence(
        issue=issue,
        requirements=requirements,
        requirements_approval=requirements_approval,
        deployment_configuration=configuration,
        deployment_digest=deployment_digest(answers),
    )


def render_approval_templates(gateway: GitHubGateway, issue_number: int) -> str:
    """Render digest-bound Japanese comments for a human to post manually."""
    issue = gateway.get_issue(issue_number)
    requirements = parse_structured_issue_requirements(
        issue.body,
        source_reference=issue.url or f"github:issue:{issue.number}",
    )
    answers = parse_structured_deployment_answers(issue.body)
    return (
        "【要件承認コメント】\n"
        "ai-dev 要件承認: 承認\n"
        f"要件ダイジェスト: {requirements_digest(requirements)}\n\n"
        "【環境構成承認コメント】\n"
        "ai-dev 環境構成承認: 承認\n"
        f"環境構成ダイジェスト: {deployment_digest(answers)}"
    )
