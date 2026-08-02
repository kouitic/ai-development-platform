from datetime import UTC, datetime, timedelta

from ai_dev_platform.application.requirements import (
    find_requirements_approval,
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.domain.models import IssueComment

STRUCTURED_REQUIREMENTS = """```yaml
requirements:
  - id: BR-001
    type: BUSINESS
    description: Satisfy the Issue request
    acceptance_criteria:
      - Required tests and reviews pass
    required: true
```"""


def approval_comment(digest: str, *, bot: bool = False) -> IssueComment:
    return IssueComment(
        body=f"ai-dev 要件承認: 承認\n要件ダイジェスト: {digest}",
        author="automation[bot]" if bot else "human-reviewer",
        author_is_bot=bot,
        created_at=datetime.now(UTC),
        url="https://github.example/issues/1#issuecomment-1",
    )


def test_matching_human_comment_approves_only_the_exact_digest() -> None:
    requirements = parse_structured_issue_requirements(
        STRUCTURED_REQUIREMENTS, source_reference="github:issue:1"
    )
    digest = requirements_digest(requirements)

    approval = find_requirements_approval(1, requirements, [approval_comment(digest)])

    assert approval is not None
    assert approval.requirements_digest == digest
    assert approval.approved_by == "human-reviewer"


def test_issue_requirement_change_invalidates_previous_approval() -> None:
    original = parse_structured_issue_requirements(
        STRUCTURED_REQUIREMENTS, source_reference="github:issue:1"
    )
    changed = parse_structured_issue_requirements(
        STRUCTURED_REQUIREMENTS.replace(
            "Required tests and reviews pass", "All required tests and reviews pass"
        ),
        source_reference="github:issue:1",
    )

    assert (
        find_requirements_approval(
            1,
            changed,
            [approval_comment(requirements_digest(original))],
        )
        is None
    )


def test_bot_comment_cannot_grant_requirements_approval() -> None:
    requirements = parse_structured_issue_requirements(
        STRUCTURED_REQUIREMENTS, source_reference="github:issue:1"
    )

    assert (
        find_requirements_approval(
            1,
            requirements,
            [approval_comment(requirements_digest(requirements), bot=True)],
        )
        is None
    )


def test_later_rejection_revokes_a_matching_approval() -> None:
    requirements = parse_structured_issue_requirements(
        STRUCTURED_REQUIREMENTS, source_reference="github:issue:1"
    )
    digest = requirements_digest(requirements)
    approved = approval_comment(digest)
    rejected = approval_comment(digest).model_copy(
        update={
            "body": f"ai-dev 要件承認: 却下\n要件ダイジェスト: {digest}",
            "created_at": approved.created_at + timedelta(seconds=1),
            "url": "https://github.example/issues/1#issuecomment-2",
        }
    )

    assert find_requirements_approval(1, requirements, [approved, rejected]) is None
