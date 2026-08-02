import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_dev_platform.domain.models import (
    ChangedFile,
    VerificationCommandResult,
    VerificationResult,
    VerificationStatus,
)
from ai_dev_platform.domain.models import (
    TestStatus as RunStatus,
)
from ai_dev_platform.infrastructure.git import (
    GitOperationError,
    MockGitWorktree,
    SafeGitWorktree,
)
from ai_dev_platform.infrastructure.github import GitHubError, MockGitHubGateway
from ai_dev_platform.infrastructure.verification import digest_worktree, snapshot_worktree


def verification(
    files: list[str],
    diff: str,
    *,
    base_sha: str = "b" * 40,
    status: VerificationStatus = VerificationStatus.PASS,
) -> VerificationResult:
    now = datetime.now(UTC)
    return VerificationResult(
        worktree_digest=digest_worktree(files, diff),
        base_commit_sha=base_sha,
        changed_files=files,
        commands=[["mock", "verify"]],
        results=[
            VerificationCommandResult(
                name="required",
                argv=["mock", "verify"],
                status=RunStatus.PASS if status == VerificationStatus.PASS else RunStatus.FAIL,
                exit_code=0 if status == VerificationStatus.PASS else 1,
                evidence_reference="verification:required",
            )
        ],
        overall_status=status,
        started_at=now,
        finished_at=now,
    )


def test_mock_github_supports_issue_pr_diff_comments_labels_and_checks() -> None:
    gateway = MockGitHubGateway(next_issue_number=10)
    issue = gateway.create_issue("Title", "Body", ["ai:ready"])
    assert gateway.get_issue(issue).body == "Body"
    gateway.update_issue(issue, "Updated")
    gateway.add_labels(issue, ["review:required"])
    gateway.remove_labels(issue, ["ai:ready"])
    assert gateway.get_issue(issue).labels == ["review:required"]

    branch = gateway.create_branch(issue, "Safe Change", "main")
    with pytest.raises(GitHubError, match="committed and pushed"):
        gateway.create_pull_request(issue, branch, "main", "PR", "Body")
    gateway.mark_branch_pushed(branch)
    pr = gateway.create_pull_request(issue, branch, "main", "PR", "Body")
    gateway.changed_files[pr] = [ChangedFile(path="src/app.py", status="modified")]
    gateway.pull_request_diffs[pr] = "diff --git a/src/app.py b/src/app.py"
    assert gateway.get_pull_request(pr).head_branch == branch
    assert gateway.get_changed_files(pr)[0].path == "src/app.py"
    assert "diff --git" in gateway.get_pull_request_diff(pr)
    assert gateway.add_issue_comment(issue, "Issue result") == "mock-issue-comment-1"
    assert gateway.add_pull_request_comment(pr, "Review result") == "mock-pr-comment-1"
    gateway.set_check_result("system-review", "a" * 40, "failure", "major finding")
    assert gateway.check_results[-1]["conclusion"] == "failure"


def test_mock_git_requires_test_commit_and_work_branch_before_push() -> None:
    worktree = MockGitWorktree(branch="main", files=["src/app.py"])
    passed = verification(worktree.files, worktree.diff_text)
    with pytest.raises(GitOperationError, match="Issue work branch"):
        worktree.commit("change", ["src/app.py"], passed)
    with pytest.raises(GitOperationError, match="pushing main"):
        worktree.push_work_branch("main")

    worktree.checkout_issue_branch("ai/issue-7-safe-change")
    failed = verification(worktree.files, worktree.diff_text, status=VerificationStatus.FAIL)
    with pytest.raises(GitOperationError, match="trusted verification"):
        worktree.commit("change", ["src/app.py"], failed)
    sha = worktree.commit("change", ["src/app.py"], passed)
    worktree.push_work_branch("ai/issue-7-safe-change")
    assert sha == "a" * 40
    assert worktree.pushed


def test_mock_git_does_not_accept_unreviewed_file_list(tmp_path: Path) -> None:
    del tmp_path
    worktree = MockGitWorktree(branch="ai/issue-8-safe-change", files=["src/reviewed.py"])
    with pytest.raises(GitOperationError, match="does not match"):
        worktree.commit(
            "change",
            ["src/other.py"],
            verification(worktree.files, worktree.diff_text),
        )


def test_safe_git_uses_argv_and_enforces_reviewed_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "app.py").write_text("print('safe')\n", encoding="utf-8")
    calls: list[list[str]] = []

    def successful_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        command = args[1:]
        if command == ["branch", "--show-current"]:
            output = "ai/issue-9-safe-change\n"
        elif command == ["status", "--porcelain=v1", "-z"]:
            output = " M src/app.py\0"
        elif command[:2] == ["diff", "--no-ext-diff"]:
            output = "diff --git a/src/app.py b/src/app.py"
        elif command == ["rev-parse", "HEAD"]:
            output = "b" * 40
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", successful_run)
    worktree = SafeGitWorktree(
        root,
        writable_patterns=["src/**"],
        protected_patterns=["docs/quality/**"],
    )
    worktree.checkout_issue_branch("ai/issue-9-safe-change")
    assert worktree.changed_files() == ["src/app.py"]
    assert worktree.assert_changed_paths_allowed() == ["src/app.py"]
    assert worktree.diff().startswith("diff --git")
    base_sha, worktree_digest = snapshot_worktree(root, ["src/app.py"])
    trusted = verification(["src/app.py"], "unused", base_sha=base_sha).model_copy(
        update={"worktree_digest": worktree_digest}
    )
    assert worktree.commit("safe change", ["src/app.py"], trusted) == "b" * 40
    worktree.push_work_branch("ai/issue-9-safe-change")
    assert all(isinstance(call, list) for call in calls)
    assert ["git", "push", "--set-upstream", "origin", "ai/issue-9-safe-change"] in calls
