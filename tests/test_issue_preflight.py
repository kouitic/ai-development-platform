from datetime import UTC, datetime, timedelta

import pytest

from ai_dev_platform.application.deployment import (
    DEPLOYMENT_QUESTIONS,
    deployment_digest,
    parse_structured_deployment_answers,
)
from ai_dev_platform.application.issue_preflight import (
    render_approval_templates,
    validate_approved_issue,
)
from ai_dev_platform.application.requirements import (
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.domain.models import IssueComment
from ai_dev_platform.infrastructure.github import MockGitHubGateway


def issue_body() -> str:
    answers = "\n".join(f"  {question.id}: 回答-{question.id}" for question in DEPLOYMENT_QUESTIONS)
    return f"""```yaml
requirements:
  - id: BR-001
    type: BUSINESS
    description: 承認済みIssueから開発する
    acceptance_criteria:
      - PRが作成される
    required: true
```

```yaml
deployment_answers:
{answers}
```"""


def approved_gateway() -> MockGitHubGateway:
    gateway = MockGitHubGateway()
    gateway.issues[12] = {
        "title": "承認済みAI開発",
        "body": issue_body(),
        "labels": ["ai:approved"],
    }
    requirements = parse_structured_issue_requirements(
        issue_body(), source_reference="mock://issues/12"
    )
    deployment = parse_structured_deployment_answers(issue_body())
    gateway.add_issue_comment(
        12,
        f"ai-dev 要件承認: 承認\n要件ダイジェスト: {requirements_digest(requirements)}",
    )
    gateway.add_issue_comment(
        12,
        f"ai-dev 環境構成承認: 承認\n環境構成ダイジェスト: {deployment_digest(deployment)}",
    )
    return gateway


def test_approved_issue_preflight_builds_ephemeral_seed_evidence() -> None:
    approved = validate_approved_issue(approved_gateway(), 12)

    assert approved.requirements_approval.approved_by == "mock-human"
    assert approved.deployment_configuration.human_approved
    assert approved.deployment_configuration.approver == "mock-human"
    assert len(approved.requirements) == 1


def test_approval_templates_are_digest_bound_and_japanese() -> None:
    rendered = render_approval_templates(approved_gateway(), 12)

    assert "ai-dev 要件承認: 承認" in rendered
    assert "ai-dev 環境構成承認: 承認" in rendered
    assert "ダイジェスト:" in rendered


@pytest.mark.parametrize("missing", ["label", "requirements", "deployment"])
def test_preflight_rejects_missing_approval_authority(missing: str) -> None:
    gateway = approved_gateway()
    if missing == "label":
        gateway.issues[12]["labels"] = []
    elif missing == "requirements":
        gateway.issue_comment_records[12] = gateway.issue_comment_records[12][1:]
    else:
        gateway.issue_comment_records[12] = gateway.issue_comment_records[12][:1]

    with pytest.raises(ValueError):
        validate_approved_issue(gateway, 12)


def test_latest_matching_deployment_rejection_revokes_approval() -> None:
    gateway = approved_gateway()
    answers = parse_structured_deployment_answers(issue_body())
    latest = datetime.now(UTC) + timedelta(seconds=5)
    gateway.issue_comment_records[12].append(
        IssueComment(
            body=(f"ai-dev 環境構成承認: 却下\n環境構成ダイジェスト: {deployment_digest(answers)}"),
            author="human-reviewer",
            created_at=latest,
            url="mock://issues/12#rejected",
        )
    )

    with pytest.raises(ValueError, match="deployment approval"):
        validate_approved_issue(gateway, 12)
