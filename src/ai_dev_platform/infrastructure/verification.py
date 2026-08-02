"""Host-managed verification bound to an exact worktree snapshot."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Protocol

from ai_dev_platform.domain.models import (
    TestStatus,
    VerificationCommand,
    VerificationCommandResult,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
)
from ai_dev_platform.security.runtime import CommandPolicy, PolicyViolation
from ai_dev_platform.security.scanner import ensure_safe_to_persist, scan_files

_REQUIRED_SECURITY_FILES = [
    "pyproject.toml",
    ".ai-dev/project.yaml",
    ".ai-dev/policies/security.yaml",
    ".ai-dev/policies/data-governance.yaml",
    ".ai-dev/policies/verification.yaml",
]


class VerificationError(RuntimeError):
    """The host could not establish a trustworthy verification result."""


class VerificationRunner(Protocol):
    """Host-side verifier; Agent output is never an implementation of this contract."""

    def run(
        self,
        root: Path,
        changed_files: list[str],
        policy: VerificationPolicy,
    ) -> VerificationResult: ...

    def is_current(
        self,
        root: Path,
        changed_files: list[str],
        result: VerificationResult,
    ) -> bool: ...


def write_verification_result(path: Path, result: VerificationResult) -> Path:
    """Write canonical verification JSON and an adjacent SHA-256 digest."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    ensure_safe_to_persist(payload.decode("utf-8"))
    path.write_bytes(payload)
    path.with_suffix(f"{path.suffix}.sha256").write_text(
        hashlib.sha256(payload).hexdigest(), encoding="ascii", newline="\n"
    )
    return path


def read_verification_result(path: Path) -> VerificationResult:
    """Read a verification result only after validating its adjacent digest."""
    path = path.resolve()
    try:
        payload = path.read_bytes()
        expected = path.with_suffix(f"{path.suffix}.sha256").read_text(encoding="ascii").strip()
    except OSError as exc:
        raise VerificationError("verification result or digest is missing") from exc
    if not expected or hashlib.sha256(payload).hexdigest() != expected:
        raise VerificationError("verification result digest mismatch")
    try:
        return VerificationResult.model_validate_json(payload)
    except ValueError as exc:
        raise VerificationError("verification result schema is invalid") from exc


def digest_worktree(changed_files: list[str], diff_text: str) -> str:
    """Return a stable digest for normalized changed paths and their exact diff."""
    normalized = sorted({Path(value).as_posix() for value in changed_files})
    digest = hashlib.sha256()
    for path in normalized:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
    digest.update(diff_text.encode("utf-8"))
    return digest.hexdigest()


def snapshot_worktree(root: Path, changed_files: list[str]) -> tuple[str, str]:
    normalized = sorted({Path(value).as_posix() for value in changed_files})
    if not normalized:
        raise VerificationError("verification requires at least one changed file")
    for value in normalized:
        candidate = (root / value).resolve(strict=False)
        try:
            candidate.relative_to(root.resolve())
        except ValueError as exc:
            raise VerificationError("changed file escapes the repository root") from exc
    try:
        base = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD", "--", *normalized],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("worktree snapshot collection failed") from exc
    if base.returncode != 0 or diff.returncode != 0:
        raise VerificationError("worktree snapshot collection failed")
    base_sha = base.stdout.strip()
    if len(base_sha) < 7:
        raise VerificationError("base commit SHA is invalid")

    # Git diff omits untracked files, so add their current bytes without persisting them.
    material = [diff.stdout]
    for relative in normalized:
        path = root / relative
        if path.is_file():
            material.append(
                f"\n--FILE:{relative}:{hashlib.sha256(path.read_bytes()).hexdigest()}--"
            )
        elif not path.exists():
            material.append(f"\n--DELETED:{relative}--")
    return base_sha, digest_worktree(normalized, "".join(material))


def snapshot_commit_range(
    root: Path,
    changed_files: list[str],
    base_commit_sha: str,
    commit_sha: str,
) -> str:
    """Digest one committed PR range and require the checked-out head to stay clean."""
    normalized = sorted({Path(value).as_posix() for value in changed_files})
    if not normalized or len(base_commit_sha) < 7 or len(commit_sha) < 7:
        raise VerificationError("committed verification target is invalid")
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
            shell=False,
        )
        diff = subprocess.run(
            [
                "git",
                "diff",
                "--no-ext-diff",
                "--binary",
                base_commit_sha,
                commit_sha,
                "--",
                *normalized,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VerificationError("committed snapshot collection failed") from exc
    if (
        head.returncode != 0
        or status.returncode != 0
        or diff.returncode != 0
        or head.stdout.strip() != commit_sha
        or status.stdout.strip()
    ):
        raise VerificationError("committed verification requires the exact clean head SHA")
    return digest_worktree(normalized, diff.stdout)


@dataclass(slots=True)
class LocalVerificationRunner:
    """Execute configured checks as argv arrays and suppress command output from evidence."""

    def _command_result(
        self, root: Path, command: VerificationCommand, policy: CommandPolicy
    ) -> VerificationCommandResult:
        started = monotonic()
        status = TestStatus.FAIL
        exit_code: int | None = None
        summary = "host verification command failed"
        try:
            argv = list(policy.validate(command.argv))
            completed = subprocess.run(
                argv,
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
                timeout=command.timeout_seconds,
                shell=False,
            )
            exit_code = completed.returncode
            if completed.returncode == 0:
                status = TestStatus.PASS
                summary = "host verification command passed"
        except (OSError, subprocess.TimeoutExpired, PolicyViolation):
            pass
        return VerificationCommandResult(
            name=command.name,
            argv=command.argv,
            required=command.required,
            status=status,
            exit_code=exit_code,
            duration_seconds=max(0, monotonic() - started),
            evidence_reference=f"verification:{command.name}",
            summary=summary,
        )

    def run(
        self,
        root: Path,
        changed_files: list[str],
        policy: VerificationPolicy,
    ) -> VerificationResult:
        root = root.resolve()
        started_at = datetime.now(UTC)
        base_sha, before_digest = snapshot_worktree(root, changed_files)
        command_policy = CommandPolicy(
            allowed_prefixes=tuple(tuple(command.argv) for command in policy.commands)
        )
        results = [
            self._command_result(root, command, command_policy) for command in policy.commands
        ]
        if policy.secret_scan:
            scan_started = monotonic()
            findings = scan_files(root, [*changed_files, *_REQUIRED_SECURITY_FILES])
            results.append(
                VerificationCommandResult(
                    name="secret-scan",
                    argv=["ai-dev-internal", "secret-scan"],
                    required=True,
                    status=TestStatus.FAIL if findings else TestStatus.PASS,
                    exit_code=1 if findings else 0,
                    duration_seconds=max(0, monotonic() - scan_started),
                    evidence_reference="verification:secret-scan",
                    summary=(
                        "secret-like content detected; values suppressed"
                        if findings
                        else "host secret scan passed"
                    ),
                )
            )
        after_base_sha, after_digest = snapshot_worktree(root, changed_files)
        invalidated = base_sha != after_base_sha or before_digest != after_digest
        required_names = {command.name for command in policy.commands if command.required}
        failed_required = any(
            result.status != TestStatus.PASS
            for result in results
            if result.name in required_names or result.name == "secret-scan"
        )
        overall = (
            VerificationStatus.INVALIDATED
            if invalidated
            else VerificationStatus.FAIL
            if failed_required
            else VerificationStatus.PASS
        )
        return VerificationResult(
            worktree_digest=before_digest,
            base_commit_sha=base_sha,
            changed_files=sorted({Path(value).as_posix() for value in changed_files}),
            commands=[command.argv for command in policy.commands],
            results=results,
            overall_status=overall,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            invalidated_reason="worktree changed during verification" if invalidated else None,
        )

    def run_committed(
        self,
        root: Path,
        changed_files: list[str],
        policy: VerificationPolicy,
        *,
        base_commit_sha: str,
        commit_sha: str,
    ) -> VerificationResult:
        """Verify an exact clean PR head for ordered CI quality gates."""
        root = root.resolve()
        started_at = datetime.now(UTC)
        before_digest = snapshot_commit_range(root, changed_files, base_commit_sha, commit_sha)
        command_policy = CommandPolicy(
            allowed_prefixes=tuple(tuple(command.argv) for command in policy.commands)
        )
        results = [
            self._command_result(root, command, command_policy) for command in policy.commands
        ]
        if policy.secret_scan:
            scan_started = monotonic()
            findings = scan_files(root, [*changed_files, *_REQUIRED_SECURITY_FILES])
            results.append(
                VerificationCommandResult(
                    name="secret-scan",
                    argv=["ai-dev-internal", "secret-scan"],
                    required=True,
                    status=TestStatus.FAIL if findings else TestStatus.PASS,
                    exit_code=1 if findings else 0,
                    duration_seconds=max(0, monotonic() - scan_started),
                    evidence_reference="verification:secret-scan",
                    summary=(
                        "secret-like content detected; values suppressed"
                        if findings
                        else "host secret scan passed"
                    ),
                )
            )
        invalidated = False
        try:
            invalidated = before_digest != snapshot_commit_range(
                root, changed_files, base_commit_sha, commit_sha
            )
        except VerificationError:
            invalidated = True
        required_names = {command.name for command in policy.commands if command.required}
        failed_required = any(
            result.status != TestStatus.PASS
            for result in results
            if result.name in required_names or result.name == "secret-scan"
        )
        overall = (
            VerificationStatus.INVALIDATED
            if invalidated
            else VerificationStatus.FAIL
            if failed_required
            else VerificationStatus.PASS
        )
        return VerificationResult(
            worktree_digest=before_digest,
            base_commit_sha=base_commit_sha,
            changed_files=sorted({Path(value).as_posix() for value in changed_files}),
            commands=[command.argv for command in policy.commands],
            results=results,
            overall_status=overall,
            started_at=started_at,
            finished_at=datetime.now(UTC),
            commit_sha=commit_sha,
            invalidated_reason="committed worktree changed during verification"
            if invalidated
            else None,
        )

    def is_current(
        self,
        root: Path,
        changed_files: list[str],
        result: VerificationResult,
    ) -> bool:
        try:
            base_sha, digest = snapshot_worktree(root.resolve(), changed_files)
        except VerificationError:
            return False
        return (
            result.overall_status == VerificationStatus.PASS
            and result.base_commit_sha == base_sha
            and result.worktree_digest == digest
            and sorted(result.changed_files)
            == sorted({Path(value).as_posix() for value in changed_files})
        )


@dataclass(slots=True)
class MockVerificationRunner:
    """Deterministic verifier for tests, with explicit snapshot invalidation controls."""

    base_commit_sha: str = "b" * 40
    diff_text: str = "mock diff"
    fail_commands: set[str] = field(default_factory=set)
    mutate_after_run: bool = False
    run_count: int = 0

    def run(
        self,
        root: Path,
        changed_files: list[str],
        policy: VerificationPolicy,
    ) -> VerificationResult:
        del root
        self.run_count += 1
        started_at = datetime.now(UTC)
        digest = digest_worktree(changed_files, self.diff_text)
        results = [
            VerificationCommandResult(
                name=command.name,
                argv=command.argv,
                required=command.required,
                status=(TestStatus.FAIL if command.name in self.fail_commands else TestStatus.PASS),
                exit_code=1 if command.name in self.fail_commands else 0,
                evidence_reference=f"mock-verification:{command.name}",
                summary="deterministic mock verification",
            )
            for command in policy.commands
        ]
        if policy.secret_scan:
            results.append(
                VerificationCommandResult(
                    name="secret-scan",
                    argv=["ai-dev-internal", "secret-scan"],
                    required=True,
                    status=TestStatus.PASS,
                    exit_code=0,
                    evidence_reference="mock-verification:secret-scan",
                    summary="deterministic mock verification",
                )
            )
        overall = (
            VerificationStatus.FAIL
            if any(result.status == TestStatus.FAIL for result in results)
            else VerificationStatus.PASS
        )
        verification = VerificationResult(
            worktree_digest=digest,
            base_commit_sha=self.base_commit_sha,
            changed_files=sorted({Path(value).as_posix() for value in changed_files}),
            commands=[command.argv for command in policy.commands],
            results=results,
            overall_status=overall,
            started_at=started_at,
            finished_at=datetime.now(UTC),
        )
        if self.mutate_after_run:
            self.diff_text = f"{self.diff_text}\nmutation-after-verification"
        return verification

    def is_current(
        self,
        root: Path,
        changed_files: list[str],
        result: VerificationResult,
    ) -> bool:
        del root
        return (
            result.overall_status == VerificationStatus.PASS
            and result.base_commit_sha == self.base_commit_sha
            and result.worktree_digest == digest_worktree(changed_files, self.diff_text)
            and sorted(result.changed_files)
            == sorted({Path(value).as_posix() for value in changed_files})
        )

    def run_committed(
        self,
        root: Path,
        changed_files: list[str],
        policy: VerificationPolicy,
        *,
        base_commit_sha: str,
        commit_sha: str,
    ) -> VerificationResult:
        """Return a deterministic committed result for offline quality-gate tests."""
        self.base_commit_sha = base_commit_sha
        result = self.run(root, changed_files, policy)
        return result.model_copy(update={"commit_sha": commit_sha})
