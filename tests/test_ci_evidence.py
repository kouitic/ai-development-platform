from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_dev_platform.application.ci_evidence import (
    collect_required_ci_evidence,
    read_ci_evidence_report,
    validate_ci_evidence_report,
    write_ci_evidence_report,
)
from ai_dev_platform.domain.models import CiEvidenceReport, GitHubCheckRunEvidence
from ai_dev_platform.infrastructure.github import MockGitHubGateway


def check_run(
    check_run_id: int,
    name: str,
    commit_sha: str,
    *,
    status: str = "completed",
    conclusion: str | None = "success",
) -> GitHubCheckRunEvidence:
    return GitHubCheckRunEvidence(
        check_run_id=check_run_id,
        name=name,
        status=status,
        conclusion=conclusion,
        commit_sha=commit_sha,
        details_url=f"mock://checks/{check_run_id}",
        completed_at=datetime.now(UTC) if status == "completed" else None,
    )


def test_collect_required_ci_evidence_selects_latest_successful_checks() -> None:
    commit_sha = "a" * 40
    gateway = MockGitHubGateway()
    gateway.commit_check_runs[commit_sha] = [
        check_run(1, "quality (3.12)", commit_sha, conclusion="failure"),
        check_run(2, "quality (3.12)", commit_sha),
        check_run(3, "quality (3.13)", commit_sha),
        check_run(4, "unrelated", commit_sha),
    ]

    report = collect_required_ci_evidence(
        gateway,
        commit_sha,
        ["quality (3.12)", "quality (3.13)"],
        timeout_seconds=1,
    )

    assert [check.check_run_id for check in report.checks] == [2, 3]
    assert all(check.commit_sha == commit_sha for check in report.checks)


@pytest.mark.parametrize(
    "checks",
    [
        [check_run(1, "quality (3.12)", "b" * 40, conclusion="failure")],
        [check_run(1, "quality (3.12)", "b" * 40)],
    ],
)
def test_collect_required_ci_evidence_rejects_failure_or_missing_check(
    checks: list[GitHubCheckRunEvidence],
) -> None:
    commit_sha = "b" * 40
    gateway = MockGitHubGateway(commit_check_runs={commit_sha: checks})

    with pytest.raises(ValueError, match="required CI"):
        collect_required_ci_evidence(
            gateway,
            commit_sha,
            ["quality (3.12)", "quality (3.13)"],
            timeout_seconds=0,
        )


def test_ci_evidence_artifact_is_digest_and_commit_protected(tmp_path: Path) -> None:
    commit_sha = "c" * 40
    report = collect_required_ci_evidence(
        MockGitHubGateway(),
        commit_sha,
        ["quality (3.12)", "quality (3.13)"],
        timeout_seconds=1,
    )
    artifact = write_ci_evidence_report(tmp_path / "ci-evidence.json", report)

    restored = read_ci_evidence_report(artifact, expected_commit_sha=commit_sha)
    assert restored == report

    artifact.write_text(artifact.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        read_ci_evidence_report(artifact, expected_commit_sha=commit_sha)


def test_validate_ci_evidence_report_rejects_malformed_evidence() -> None:
    commit_sha = "d" * 40
    valid_check = check_run(1, "quality (3.12)", commit_sha)

    duplicate_requirements = CiEvidenceReport(
        commit_sha=commit_sha,
        required_check_names=["quality (3.12)", "quality (3.12)"],
        checks=[valid_check],
    )
    with pytest.raises(ValueError, match="must be unique"):
        validate_ci_evidence_report(duplicate_requirements)

    missing_check = CiEvidenceReport(
        commit_sha=commit_sha,
        required_check_names=["quality (3.12)", "quality (3.13)"],
        checks=[valid_check],
    )
    with pytest.raises(ValueError, match="exactly match"):
        validate_ci_evidence_report(missing_check)

    incomplete_check = check_run(2, "quality (3.12)", commit_sha, status="in_progress")
    incomplete_report = CiEvidenceReport(
        commit_sha=commit_sha,
        required_check_names=["quality (3.12)"],
        checks=[incomplete_check],
    )
    with pytest.raises(ValueError, match="completed success"):
        validate_ci_evidence_report(incomplete_report)


def test_read_ci_evidence_report_rejects_missing_and_wrong_commit(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="artifact is missing"):
        read_ci_evidence_report(missing, expected_commit_sha="e" * 40)

    commit_sha = "e" * 40
    report = collect_required_ci_evidence(
        MockGitHubGateway(),
        commit_sha,
        ["quality (3.12)", "quality (3.13)"],
        timeout_seconds=1,
    )
    artifact = write_ci_evidence_report(tmp_path / "ci-evidence.json", report)
    with pytest.raises(ValueError, match="belongs to another commit"):
        read_ci_evidence_report(artifact, expected_commit_sha="f" * 40)
