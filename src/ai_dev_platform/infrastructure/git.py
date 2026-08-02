"""Controlled Git worktree operations exposed as reviewed methods, never shell text."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ai_dev_platform.domain.models import VerificationResult, VerificationStatus
from ai_dev_platform.infrastructure.verification import digest_worktree, snapshot_worktree
from ai_dev_platform.security.paths import assert_write_allowed


class GitOperationError(RuntimeError):
    """A Git operation was unsafe or failed with details suppressed."""


class GitWorktreeGateway(Protocol):
    """Narrow Git contract used by the orchestration layer."""

    def current_branch(self) -> str: ...

    def checkout_issue_branch(self, branch: str) -> None: ...

    def changed_files(self) -> list[str]: ...

    def diff(self) -> str: ...

    def commit(
        self,
        message: str,
        files: list[str],
        verification: VerificationResult,
        *,
        protected_path_approved: bool = False,
    ) -> str: ...

    def push_work_branch(self, branch: str) -> None: ...


def _is_work_branch(branch: str) -> bool:
    return bool(re.fullmatch(r"ai/issue-\d+-[a-z0-9-]+", branch))


class SafeGitWorktree:
    """Execute only bounded Git operations against one resolved repository root."""

    def __init__(
        self,
        root: Path,
        *,
        writable_patterns: list[str],
        protected_patterns: list[str],
        executable: str = "git",
    ) -> None:
        self.root = root.resolve()
        self.writable_patterns = writable_patterns
        self.protected_patterns = protected_patterns
        self.executable = executable

    def _run(self, args: list[str], *, timeout: int = 60) -> str:
        if any(value in {"--force", "-f", "--force-with-lease"} for value in args):
            raise GitOperationError("force push is forbidden")
        try:
            result = subprocess.run(
                [self.executable, *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitOperationError("Git execution failed") from exc
        if result.returncode != 0:
            raise GitOperationError("Git returned an error; details were suppressed")
        return result.stdout.rstrip("\r\n")

    def current_branch(self) -> str:
        """Return the checked-out branch."""
        return self._run(["branch", "--show-current"])

    def checkout_issue_branch(self, branch: str) -> None:
        """Checkout only a predictable Issue work branch."""
        if not _is_work_branch(branch):
            raise GitOperationError("only Issue work branches can be checked out")
        self._run(["fetch", "origin", branch])
        self._run(["checkout", branch])

    def changed_files(self) -> list[str]:
        """Return normalized changed paths without interpreting shell syntax."""
        output = self._run(["status", "--porcelain=v1", "-z"])
        changed: list[str] = []
        for record in output.split("\0"):
            if not record:
                continue
            path = record[3:]
            if " -> " in path:
                path = path.split(" -> ", maxsplit=1)[1]
            changed.append(path.replace("\\", "/"))
        return changed

    def assert_changed_paths_allowed(self, *, protected_path_approved: bool = False) -> list[str]:
        """Reject repository escape, symlink bypass, and unauthorized protected files."""
        files = self.changed_files()
        for relative in files:
            assert_write_allowed(
                self.root,
                self.root / relative,
                self.writable_patterns,
                self.protected_patterns,
                protected_path_approved=protected_path_approved,
            )
        return files

    def diff(self) -> str:
        """Return the current worktree and index diff."""
        return self._run(["diff", "--no-ext-diff", "--binary", "HEAD"], timeout=120)

    def commit(
        self,
        message: str,
        files: list[str],
        verification: VerificationResult,
        *,
        protected_path_approved: bool = False,
    ) -> str:
        """Commit reviewed paths only when trusted verification still matches them."""
        branch = self.current_branch()
        if not _is_work_branch(branch) or branch == "main":
            raise GitOperationError("commits are allowed only on an Issue work branch")
        if not isinstance(verification, VerificationResult):
            raise GitOperationError("agent-reported tests are not trusted verification")
        if verification.overall_status != VerificationStatus.PASS:
            raise GitOperationError("successful trusted verification is needed before commit")
        changed = set(
            self.assert_changed_paths_allowed(protected_path_approved=protected_path_approved)
        )
        normalized_files: list[str] = []
        for value in files:
            relative = assert_write_allowed(
                self.root,
                self.root / value,
                self.writable_patterns,
                self.protected_patterns,
                protected_path_approved=protected_path_approved,
            )
            normalized_files.append(str(relative))
        if not normalized_files or set(normalized_files) != changed:
            raise GitOperationError("commit file list does not match the reviewed changes")
        if sorted(verification.changed_files) != sorted(changed):
            raise GitOperationError("verification does not match the changed file set")
        current_base, current_digest = snapshot_worktree(self.root, sorted(changed))
        if verification.base_commit_sha != current_base:
            raise GitOperationError("verification base commit is stale")
        if verification.worktree_digest != current_digest:
            raise GitOperationError("worktree changed after verification")
        self._run(["add", "--", *normalized_files])
        self._run(["commit", "-m", message, "--", *normalized_files], timeout=120)
        sha = self._run(["rev-parse", "HEAD"])
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise GitOperationError("Git did not return a valid commit SHA")
        return sha

    def push_work_branch(self, branch: str) -> None:
        """Push only the currently checked-out Issue branch without force options."""
        if not _is_work_branch(branch) or branch == "main":
            raise GitOperationError("pushing main or non-Issue branches is forbidden")
        if self.current_branch() != branch:
            raise GitOperationError("only the checked-out work branch can be pushed")
        self._run(["push", "--set-upstream", "origin", branch], timeout=120)


@dataclass(slots=True)
class MockGitWorktree:
    """Deterministic Git worktree for E2E tests without a repository or subprocess."""

    branch: str = ""
    files: list[str] = field(default_factory=list)
    diff_text: str = ""
    committed_sha: str = ""
    pushed: bool = False
    fail_operation: str | None = None
    base_commit_sha: str = "b" * 40

    def _check(self, operation: str) -> None:
        if self.fail_operation == operation:
            raise GitOperationError(f"mock {operation} failure")

    def current_branch(self) -> str:
        return self.branch

    def checkout_issue_branch(self, branch: str) -> None:
        self._check("checkout")
        if not _is_work_branch(branch):
            raise GitOperationError("only Issue work branches can be checked out")
        self.branch = branch

    def changed_files(self) -> list[str]:
        return list(self.files)

    def diff(self) -> str:
        return self.diff_text

    def commit(
        self,
        message: str,
        files: list[str],
        verification: VerificationResult,
        *,
        protected_path_approved: bool = False,
    ) -> str:
        del message, protected_path_approved
        self._check("commit")
        if not _is_work_branch(self.branch) or self.branch == "main":
            raise GitOperationError("commits are allowed only on an Issue work branch")
        if set(files) != set(self.files):
            raise GitOperationError("commit file list does not match changes")
        if not isinstance(verification, VerificationResult):
            raise GitOperationError("agent-reported tests are not trusted verification")
        if verification.overall_status != VerificationStatus.PASS:
            raise GitOperationError("successful trusted verification is needed before commit")
        if verification.base_commit_sha != self.base_commit_sha:
            raise GitOperationError("verification base commit is stale")
        if sorted(verification.changed_files) != sorted(self.files):
            raise GitOperationError("verification does not match the changed file set")
        if verification.worktree_digest != digest_worktree(self.files, self.diff_text):
            raise GitOperationError("worktree changed after verification")
        self.committed_sha = "a" * 40
        return self.committed_sha

    def push_work_branch(self, branch: str) -> None:
        self._check("push")
        if branch != self.branch or not _is_work_branch(branch) or branch == "main":
            raise GitOperationError("pushing main or a different branch is forbidden")
        if not self.committed_sha:
            raise GitOperationError("commit is required before push")
        self.pushed = True
