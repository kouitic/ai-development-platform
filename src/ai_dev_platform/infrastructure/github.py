"""GitHub gateway abstractions with a safe ``gh`` CLI implementation."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from ai_dev_platform.domain.models import ChangedFile, IssueComment, IssueData, PullRequestData
from ai_dev_platform.security.scanner import ensure_safe_to_persist


class GitHubError(RuntimeError):
    """Sanitized GitHub operation error."""


class GitHubGateway(Protocol):
    """GitHub operations needed by the MVP; merge and production APIs are omitted."""

    def get_issue(self, issue_number: int) -> IssueData: ...

    def get_issue_comments(self, issue_number: int) -> list[IssueComment]: ...

    def create_issue(self, title: str, body: str, labels: list[str]) -> int: ...

    def update_issue(self, issue_number: int, body: str) -> None: ...

    def add_issue_comment(self, issue_number: int, body: str) -> str: ...

    def create_branch(
        self, issue_number: int, description: str, base_branch: str = "main"
    ) -> str: ...

    def get_pull_request(self, pr_number: int) -> PullRequestData: ...

    def create_pull_request(
        self,
        issue_number: int,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int: ...

    def add_pull_request_comment(self, pr_number: int, body: str) -> str: ...

    def get_pull_request_diff(self, pr_number: int) -> str: ...

    def get_changed_files(self, pr_number: int) -> list[ChangedFile]: ...

    def add_labels(self, issue_number: int, labels: list[str]) -> None: ...

    def remove_labels(self, issue_number: int, labels: list[str]) -> None: ...

    def set_check_result(
        self, name: str, commit_sha: str, conclusion: str, summary: str
    ) -> None: ...


def issue_branch_name(issue_number: int, description: str) -> str:
    """Return a predictable GitHub Flow branch name."""
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower()).strip("-")[:50] or "task"
    return f"ai/issue-{issue_number}-{slug}"


@dataclass(slots=True)
class MockGitHubGateway:
    """In-memory gateway used by offline runs and external-API-free E2E tests."""

    next_issue_number: int = 1
    next_pull_request_number: int = 1
    issues: dict[int, dict[str, object]] = field(default_factory=dict)
    pull_requests: dict[int, dict[str, object]] = field(default_factory=dict)
    issue_comments: list[tuple[int, str]] = field(default_factory=list)
    issue_comment_records: dict[int, list[IssueComment]] = field(default_factory=dict)
    pull_request_comments: list[tuple[int, str]] = field(default_factory=list)
    comments: list[tuple[int, str]] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    check_results: list[dict[str, str]] = field(default_factory=list)
    changed_files: dict[int, list[ChangedFile]] = field(default_factory=dict)
    pull_request_diffs: dict[int, str] = field(default_factory=dict)
    pushed_branches: set[str] = field(default_factory=set)
    pushed_branch_shas: dict[str, str] = field(default_factory=dict)
    fail_with: str | None = None

    def _maybe_fail(self) -> None:
        if self.fail_with:
            raise GitHubError(self.fail_with)

    def get_issue(self, issue_number: int) -> IssueData:
        self._maybe_fail()
        raw = self.issues.get(issue_number)
        if raw is None:
            raise GitHubError("Issue was not found")
        raw_labels = raw.get("labels", [])
        labels = [str(value) for value in raw_labels] if isinstance(raw_labels, list) else []
        return IssueData(
            number=issue_number,
            title=str(raw["title"]),
            body=str(raw["body"]),
            labels=labels,
            url=f"mock://issues/{issue_number}",
        )

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        self._maybe_fail()
        ensure_safe_to_persist(body)
        number = self.next_issue_number
        self.next_issue_number += 1
        self.issues[number] = {"title": title, "body": body, "labels": list(labels)}
        return number

    def get_issue_comments(self, issue_number: int) -> list[IssueComment]:
        self._maybe_fail()
        if issue_number not in self.issues:
            raise GitHubError("Issue was not found")
        return list(self.issue_comment_records.get(issue_number, []))

    def update_issue(self, issue_number: int, body: str) -> None:
        self._maybe_fail()
        ensure_safe_to_persist(body)
        if issue_number not in self.issues:
            raise GitHubError("Issue was not found")
        self.issues[issue_number]["body"] = body

    def add_issue_comment(self, issue_number: int, body: str) -> str:
        self._maybe_fail()
        ensure_safe_to_persist(body)
        self.issue_comments.append((issue_number, body))
        self.comments.append((issue_number, body))
        comment_id = f"mock-issue-comment-{len(self.issue_comments)}"
        reference = f"mock://issues/{issue_number}#{comment_id}"
        self.issue_comment_records.setdefault(issue_number, []).append(
            IssueComment(
                body=body,
                author="mock-human",
                created_at=datetime.now(UTC),
                url=reference,
            )
        )
        return comment_id

    def add_comment(self, issue_number: int, body: str) -> None:
        """Compatibility alias for the original MVP contract."""
        self.add_issue_comment(issue_number, body)

    def create_branch(self, issue_number: int, description: str, base_branch: str = "main") -> str:
        self._maybe_fail()
        if not base_branch:
            raise GitHubError("base branch is required")
        branch = issue_branch_name(issue_number, description)
        self.branches.append(branch)
        return branch

    def get_pull_request(self, pr_number: int) -> PullRequestData:
        self._maybe_fail()
        raw = self.pull_requests.get(pr_number)
        if raw is None:
            raise GitHubError("Pull request was not found")
        return PullRequestData.model_validate(raw)

    def create_pull_request(
        self,
        issue_number: int,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        self._maybe_fail()
        ensure_safe_to_persist(body)
        if branch not in self.pushed_branches:
            raise GitHubError("branch must be committed and pushed before PR creation")
        if branch == base_branch or branch == "main":
            raise GitHubError("Pull request head must be a work branch")
        number = self.next_pull_request_number
        self.next_pull_request_number += 1
        self.pull_requests[number] = PullRequestData(
            number=number,
            title=title,
            body=body,
            head_branch=branch,
            base_branch=base_branch,
            head_sha=self.pushed_branch_shas.get(
                branch, "mock000000000000000000000000000000000000"
            ),
            url=f"mock://pulls/{number}",
        ).model_dump(mode="json")
        return number

    def mark_branch_pushed(self, branch: str, commit_sha: str | None = None) -> None:
        """Record the mock equivalent of a successful work-branch push."""
        if branch == "main":
            raise GitHubError("pushing main is forbidden")
        self.pushed_branches.add(branch)
        if commit_sha is not None:
            self.pushed_branch_shas[branch] = commit_sha

    def add_pull_request_comment(self, pr_number: int, body: str) -> str:
        self._maybe_fail()
        ensure_safe_to_persist(body)
        if pr_number not in self.pull_requests:
            raise GitHubError("Pull request was not found")
        self.pull_request_comments.append((pr_number, body))
        return f"mock-pr-comment-{len(self.pull_request_comments)}"

    def get_pull_request_diff(self, pr_number: int) -> str:
        self._maybe_fail()
        if pr_number not in self.pull_requests:
            raise GitHubError("Pull request was not found")
        return self.pull_request_diffs.get(pr_number, "")

    def get_changed_files(self, pr_number: int) -> list[ChangedFile]:
        self._maybe_fail()
        if pr_number not in self.pull_requests:
            raise GitHubError("Pull request was not found")
        return list(self.changed_files.get(pr_number, []))

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        self._maybe_fail()
        if issue_number not in self.issues:
            raise GitHubError("Issue was not found")
        raw_labels = self.issues[issue_number].get("labels", [])
        current = [str(value) for value in raw_labels] if isinstance(raw_labels, list) else []
        self.issues[issue_number]["labels"] = list(dict.fromkeys([*current, *labels]))

    def remove_labels(self, issue_number: int, labels: list[str]) -> None:
        self._maybe_fail()
        if issue_number not in self.issues:
            raise GitHubError("Issue was not found")
        removed = set(labels)
        raw_labels = self.issues[issue_number].get("labels", [])
        current = [str(value) for value in raw_labels] if isinstance(raw_labels, list) else []
        self.issues[issue_number]["labels"] = [value for value in current if value not in removed]

    def set_check_result(self, name: str, commit_sha: str, conclusion: str, summary: str) -> None:
        self._maybe_fail()
        ensure_safe_to_persist(summary)
        if conclusion not in {"success", "failure", "neutral", "action_required"}:
            raise GitHubError("unsupported check conclusion")
        self.check_results.append(
            {"name": name, "commit_sha": commit_sha, "conclusion": conclusion, "summary": summary}
        )


class GhCliGateway:
    """Use ``gh`` with argument arrays, bounded calls, and sanitized errors."""

    def __init__(self, root: Path, executable: str = "gh") -> None:
        self.root = root.resolve()
        self.executable = executable

    def _run(self, args: list[str]) -> str:
        try:
            result = subprocess.run(
                [self.executable, *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise GitHubError("GitHub CLI execution failed") from exc
        if result.returncode != 0:
            raise GitHubError("GitHub CLI returned an error; sensitive details were suppressed")
        return result.stdout.strip()

    def _json(self, args: list[str]) -> dict[str, Any]:
        try:
            value = json.loads(self._run(args))
        except json.JSONDecodeError as exc:
            raise GitHubError("GitHub CLI returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise GitHubError("GitHub CLI returned an unexpected JSON shape")
        return value

    def get_issue(self, issue_number: int) -> IssueData:
        raw = self._json(
            ["issue", "view", str(issue_number), "--json", "number,title,body,labels,url"]
        )
        labels = raw.get("labels", [])
        return IssueData(
            number=int(raw["number"]),
            title=str(raw.get("title", "")),
            body=str(raw.get("body", "")),
            labels=[str(item.get("name", "")) for item in labels if isinstance(item, dict)],
            url=str(raw.get("url", "")),
        )

    def get_issue_comments(self, issue_number: int) -> list[IssueComment]:
        raw = self._json(["issue", "view", str(issue_number), "--json", "comments"])
        comments = raw.get("comments", [])
        result: list[IssueComment] = []
        for item in comments:
            if not isinstance(item, dict):
                continue
            author = item.get("author", {})
            login = str(author.get("login", "")) if isinstance(author, dict) else ""
            created_at = str(item.get("createdAt", ""))
            url = str(item.get("url", ""))
            if not login or not created_at or not url:
                continue
            try:
                parsed_at = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise GitHubError("GitHub comment timestamp is invalid") from exc
            result.append(
                IssueComment(
                    body=str(item.get("body", "")),
                    author=login,
                    created_at=parsed_at,
                    url=url,
                    author_is_bot=login.lower().endswith("[bot]"),
                )
            )
        return result

    def create_issue(self, title: str, body: str, labels: list[str]) -> int:
        ensure_safe_to_persist(body)
        args = ["issue", "create", "--title", title, "--body", body]
        for label in labels:
            args.extend(["--label", label])
        output = self._run(args)
        match = re.search(r"/(\d+)$", output)
        if not match:
            raise GitHubError("GitHub CLI did not return an Issue number")
        return int(match.group(1))

    def update_issue(self, issue_number: int, body: str) -> None:
        ensure_safe_to_persist(body)
        self._run(["issue", "edit", str(issue_number), "--body", body])

    def add_issue_comment(self, issue_number: int, body: str) -> str:
        ensure_safe_to_persist(body)
        return self._run(["issue", "comment", str(issue_number), "--body", body])

    def add_comment(self, issue_number: int, body: str) -> None:
        """Compatibility alias for the original MVP contract."""
        self.add_issue_comment(issue_number, body)

    def create_branch(self, issue_number: int, description: str, base_branch: str = "main") -> str:
        branch = issue_branch_name(issue_number, description)
        sha = self._run(
            [
                "api",
                f"repos/{{owner}}/{{repo}}/git/ref/heads/{base_branch}",
                "--jq",
                ".object.sha",
            ]
        )
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise GitHubError("GitHub CLI did not return a valid base commit")
        self._run(
            [
                "api",
                "repos/{owner}/{repo}/git/refs",
                "-f",
                f"ref=refs/heads/{branch}",
                "-f",
                f"sha={sha}",
            ]
        )
        return branch

    def get_pull_request(self, pr_number: int) -> PullRequestData:
        raw = self._json(
            [
                "pr",
                "view",
                str(pr_number),
                "--json",
                "number,title,body,headRefName,baseRefName,headRefOid,url",
            ]
        )
        return PullRequestData(
            number=int(raw["number"]),
            title=str(raw.get("title", "")),
            body=str(raw.get("body", "")),
            head_branch=str(raw.get("headRefName", "")),
            base_branch=str(raw.get("baseRefName", "")),
            head_sha=str(raw.get("headRefOid", "")),
            url=str(raw.get("url", "")),
        )

    def create_pull_request(
        self,
        issue_number: int,
        branch: str,
        base_branch: str,
        title: str,
        body: str,
    ) -> int:
        ensure_safe_to_persist(body)
        if branch in {base_branch, "main"}:
            raise GitHubError("Pull request head must be a work branch")
        output = self._run(
            [
                "pr",
                "create",
                "--head",
                branch,
                "--base",
                base_branch,
                "--title",
                title,
                "--body",
                f"{body}\n\nCloses #{issue_number}",
            ]
        )
        match = re.search(r"/(\d+)$", output)
        if not match:
            raise GitHubError("GitHub CLI did not return a Pull Request number")
        return int(match.group(1))

    def add_pull_request_comment(self, pr_number: int, body: str) -> str:
        ensure_safe_to_persist(body)
        return self._run(["pr", "comment", str(pr_number), "--body", body])

    def get_pull_request_diff(self, pr_number: int) -> str:
        return self._run(["pr", "diff", str(pr_number)])

    def get_changed_files(self, pr_number: int) -> list[ChangedFile]:
        raw = self._json(["pr", "view", str(pr_number), "--json", "files"])
        status_map = {
            "ADDED": "added",
            "MODIFIED": "modified",
            "REMOVED": "removed",
            "RENAMED": "renamed",
            "COPIED": "copied",
            "CHANGED": "changed",
        }
        files = raw.get("files", [])
        return [
            ChangedFile(
                path=str(item.get("path", "")),
                status=status_map.get(str(item.get("status", "CHANGED")), "changed"),  # type: ignore[arg-type]
                additions=int(item.get("additions", 0)),
                deletions=int(item.get("deletions", 0)),
            )
            for item in files
            if isinstance(item, dict)
        ]

    def add_labels(self, issue_number: int, labels: list[str]) -> None:
        if not labels:
            return
        self._run(["issue", "edit", str(issue_number), "--add-label", ",".join(labels)])

    def remove_labels(self, issue_number: int, labels: list[str]) -> None:
        if not labels:
            return
        self._run(["issue", "edit", str(issue_number), "--remove-label", ",".join(labels)])

    def set_check_result(self, name: str, commit_sha: str, conclusion: str, summary: str) -> None:
        ensure_safe_to_persist(summary)
        if conclusion not in {"success", "failure", "neutral", "action_required"}:
            raise GitHubError("unsupported check conclusion")
        self._run(
            [
                "api",
                "repos/{owner}/{repo}/check-runs",
                "-X",
                "POST",
                "-f",
                f"name={name}",
                "-f",
                f"head_sha={commit_sha}",
                "-f",
                "status=completed",
                "-f",
                f"conclusion={conclusion}",
                "-f",
                f"output[title]={name}",
                "-f",
                f"output[summary]={summary}",
            ]
        )
