import hashlib
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_dev_platform.application.quality_artifacts import (
    QualityArtifactError,
    read_quality_artifact,
)
from ai_dev_platform.application.quality_gate import run_integrated_quality_gates
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    ChangedFile,
    Decision,
    DeveloperResult,
    EvidenceReference,
    PullRequestData,
    ReviewType,
    TaskEvidence,
    TaskRecord,
    VerificationCommand,
    VerificationCommandResult,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from ai_dev_platform.domain.models import (
    TestRunResult as AgentTestRunResult,
)
from ai_dev_platform.domain.models import (
    TestStatus as RunStatus,
)
from ai_dev_platform.infrastructure.git import MockGitWorktree
from ai_dev_platform.infrastructure.github import MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.infrastructure.verification import (
    LocalVerificationRunner,
    MockVerificationRunner,
    VerificationError,
    digest_worktree,
    read_verification_result,
    snapshot_commit_range,
    snapshot_worktree,
    write_verification_result,
)
from ai_dev_platform.providers.mock import MockAgentProvider


def _run_git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, shell=False)


def _git_project(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "test@example.invalid")
    _run_git(root, "config", "user.name", "Test")
    (root / "app.py").write_text("value = 1\n", encoding="utf-8")
    _run_git(root, "add", "app.py")
    _run_git(root, "commit", "-m", "initial")
    (root / "app.py").write_text("value = 2\n", encoding="utf-8")
    return root


def _policy(*, passing: bool = True) -> VerificationPolicy:
    code = "raise SystemExit(0)" if passing else "raise SystemExit(1)"
    return VerificationPolicy(
        commands=[
            VerificationCommand(
                name="project-check",
                argv=["python", "-c", code],
                timeout_seconds=30,
            )
        ],
        secret_scan=False,
    )


def _trusted_result(commit_sha: str, files: list[str], diff: str) -> VerificationResult:
    now = datetime.now(UTC)
    return VerificationResult(
        worktree_digest=digest_worktree(files, diff),
        base_commit_sha="b" * 40,
        changed_files=files,
        commands=[["mock", "verify"]],
        results=[
            VerificationCommandResult(
                name="required",
                argv=["mock", "verify"],
                status=RunStatus.PASS,
                exit_code=0,
                evidence_reference="verification:required",
            )
        ],
        overall_status=VerificationStatus.PASS,
        started_at=now,
        finished_at=now,
        commit_sha=commit_sha,
    )


def test_local_verification_is_bound_to_post_change_snapshot(tmp_path: Path) -> None:
    root = _git_project(tmp_path)
    runner = LocalVerificationRunner()
    result = runner.run(root, ["app.py"], _policy())
    assert result.overall_status == VerificationStatus.PASS
    assert result.changed_files == ["app.py"]
    assert result.commands == [["python", "-c", "raise SystemExit(0)"]]
    assert runner.is_current(root, ["app.py"], result)

    (root / "app.py").write_text("value = 3\n", encoding="utf-8")
    assert not runner.is_current(root, ["app.py"], result)


def test_local_verification_failure_and_digest_protected_result(tmp_path: Path) -> None:
    root = _git_project(tmp_path)
    result = LocalVerificationRunner().run(root, ["app.py"], _policy(passing=False))
    assert result.overall_status == VerificationStatus.FAIL
    assert result.results[0].status == RunStatus.FAIL

    path = write_verification_result(tmp_path / "verification.json", result)
    assert read_verification_result(path).run_id == result.run_id
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(VerificationError, match="digest mismatch"):
        read_verification_result(path)


def test_local_verification_binds_a_clean_committed_range(tmp_path: Path) -> None:
    root = _git_project(tmp_path)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()
    _run_git(root, "add", "app.py")
    _run_git(root, "commit", "-m", "change app")
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()

    result = LocalVerificationRunner().run_committed(
        root,
        ["app.py"],
        _policy(),
        base_commit_sha=base_sha,
        commit_sha=commit_sha,
    )

    assert result.overall_status == VerificationStatus.PASS
    assert result.base_commit_sha == base_sha
    assert result.commit_sha == commit_sha
    assert result.results[0].status == RunStatus.PASS
    assert result.invalidated_reason is None


def test_verification_rejects_missing_invalid_and_escaping_inputs(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(VerificationError, match="missing"):
        read_verification_result(missing)

    invalid = tmp_path / "invalid.json"
    payload = b"{}"
    invalid.write_bytes(payload)
    invalid.with_suffix(".json.sha256").write_text(
        hashlib.sha256(payload).hexdigest(), encoding="ascii"
    )
    with pytest.raises(VerificationError, match="schema is invalid"):
        read_verification_result(invalid)

    with pytest.raises(VerificationError, match="at least one changed file"):
        snapshot_worktree(tmp_path, [])
    with pytest.raises(VerificationError, match="escapes the repository root"):
        snapshot_worktree(tmp_path, ["../outside.py"])
    with pytest.raises(VerificationError, match="target is invalid"):
        snapshot_commit_range(tmp_path, [], "short", "short")


@pytest.mark.parametrize("mutate_after_run,fail_command", [(True, False), (False, True)])
def test_workflow_never_commits_stale_or_failed_verification(
    initialized_project: Path,
    mutate_after_run: bool,
    fail_command: bool,
) -> None:
    loaded = load_config(initialized_project)
    assert loaded.verification is not None
    store = SQLiteStateStore(
        initialized_project / ".ai-dev" / "local" / f"verify-{mutate_after_run}.sqlite3"
    )
    developer = DeveloperResult(
        decision=Decision.PASS,
        summary="agent claims success",
        evidence=[EvidenceReference(id="agent-evidence", kind="mock", reference="agent")],
        changed_files=["src/app.py"],
        test_results=[
            AgentTestRunResult(
                name="agent-self-report",
                status=RunStatus.PASS,
                evidence_reference="agent-evidence",
                passed=1,
                commit_sha="c" * 40,
            )
        ],
    )
    task = store.create_task(
        TaskRecord(
            task_id=f"verify-{mutate_after_run}-{fail_command}",
            issue_number=31 if mutate_after_run else 32,
            state=WorkflowState.AUTOMATED_TESTING,
            commit_sha="c" * 40,
            branch="ai/issue-31-verify",
            evidence=TaskEvidence(
                developer_results=[developer],
                agent_reported_test_results=developer.test_results,
            ),
        )
    )
    github = MockGitHubGateway()
    github.issues[task.issue_number] = {"title": "Issue", "body": "Body", "labels": []}
    git = MockGitWorktree(
        branch=task.branch,
        files=["src/app.py"],
        diff_text="post-agent diff",
    )
    verifier = MockVerificationRunner(
        base_commit_sha=git.base_commit_sha,
        diff_text=git.diff_text,
        fail_commands={"pytest"} if fail_command else set(),
        mutate_after_run=mutate_after_run,
    )
    runner = WorkflowRunner(
        loaded.project,
        loaded.agents,
        MockAgentProvider(),
        store,
        root=initialized_project,
        github=github,
        git=git,
        verification_runner=verifier,
        verification_policy=loaded.verification,
    )
    finished = runner._run_verification_stage(task)
    assert verifier.run_count == 1
    assert finished.state == WorkflowState.REWORK_REQUIRED
    assert not git.committed_sha
    assert finished.evidence.agent_reported_test_results[0].status == RunStatus.PASS
    assert finished.evidence.trusted_verification_results[-1].overall_status in {
        VerificationStatus.FAIL,
        VerificationStatus.INVALIDATED,
    }


def test_integrated_quality_gates_run_once_and_reject_artifact_sha_mismatch(
    initialized_project: Path,
) -> None:
    loaded = load_config(initialized_project)
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "integrated.sqlite3")
    gateway = MockGitHubGateway()
    gateway.issues[41] = {
        "title": "Issue",
        "body": """```yaml
requirements:
  - id: BR-001
    type: BUSINESS
    description: Review the implementation
    acceptance_criteria:
      - Trusted verification passes
    required: true
```""",
        "labels": [],
    }
    gateway.pull_requests[7] = PullRequestData(
        number=7,
        title="PR",
        head_branch="ai/issue-41-review",
        base_branch="main",
        head_sha="a" * 40,
    ).model_dump(mode="json")
    gateway.changed_files[7] = [ChangedFile(path="src/app.py", status="modified")]
    gateway.pull_request_diffs[7] = "reviewed diff"
    verification = _trusted_result("a" * 40, ["src/app.py"], "reviewed diff")
    provider = MockAgentProvider()
    artifact_dir = initialized_project / ".ai-dev" / "local" / "artifacts"

    finished = run_integrated_quality_gates(
        loaded,
        provider,
        store,
        gateway,
        initialized_project,
        issue_number=41,
        pull_request_number=7,
        verification=verification,
        artifact_directory=artifact_dir,
    )
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert [request.agent_id for request in provider.requests] == [
        "system-reviewer",
        "business-reviewer",
        "qa",
    ]
    assert [item["name"] for item in gateway.check_results] == [
        "ai-quality/system-review",
        "ai-quality/business-review",
        "ai-quality/qa-assessment",
        "ai-quality/final",
    ]
    assert all(
        review.reviewed_commit_sha == "a" * 40
        for review in [
            finished.evidence.system_reviews[-1],
            finished.evidence.business_reviews[-1],
            finished.evidence.qa_assessments[-1],
        ]
    )
    system_path = artifact_dir / "system-review.json"
    with pytest.raises(QualityArtifactError, match="commit SHA mismatch"):
        read_quality_artifact(
            system_path,
            expected_stage=ReviewType.SYSTEM,
            issue_number=41,
            pull_request_number=7,
            commit_sha="d" * 40,
        )
    system_path.write_text("{}", encoding="utf-8")
    with pytest.raises(QualityArtifactError, match="digest mismatch"):
        read_quality_artifact(
            system_path,
            expected_stage=ReviewType.SYSTEM,
            issue_number=41,
            pull_request_number=7,
            commit_sha="a" * 40,
        )
