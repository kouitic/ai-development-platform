import asyncio
import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ai_dev_platform.application.quality_artifacts import (
    QualityArtifactError,
    read_quality_artifact,
)
from ai_dev_platform.application.quality_gate import (
    _assert_verification_target,
    _collect_host_validated_traceability,
    _developer_traceability_failure_message,
    prepare_quality_task,
    run_integrated_quality_gates,
)
from ai_dev_platform.application.requirements import (
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.application.traceability import (
    collect_design_reference_candidates,
    collect_implementation_reference_candidates,
)
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import LoadedConfig, load_config
from ai_dev_platform.domain.models import (
    AcceptanceCriterionTestMapping,
    AgentResult,
    AgentRunStatus,
    ChangedFile,
    Decision,
    DeveloperResult,
    EvidenceReference,
    ExecutedTestCase,
    PullRequestData,
    RequirementImplementationReference,
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


def test_developer_traceability_failure_message_is_diagnostic_and_sanitized() -> None:
    api_error = AgentResult(
        status=AgentRunStatus.ERROR,
        error_code="provider_api_error_429",
        summary="sensitive response detail",
    )
    assert _developer_traceability_failure_message(api_error) == (
        "developer traceability collection failed: status=ERROR; code=provider_api_error_429"
    )

    unsafe_error = api_error.model_copy(update={"error_code": "unsafe\nsecret=value"})
    message = _developer_traceability_failure_message(unsafe_error)
    assert "secret" not in message
    assert message.endswith("code=provider_failure")


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


def test_design_reference_candidates_are_commit_bound_and_unprotected(
    tmp_path: Path,
) -> None:
    root = tmp_path / "design-references"
    root.mkdir()
    _run_git(root, "init")
    _run_git(root, "config", "user.email", "test@example.invalid")
    _run_git(root, "config", "user.name", "Test")
    design_document = root / "docs" / "design" / "current.md"
    protected_document = root / "docs" / "quality" / "protected.md"
    implementation = root / "src" / "app.py"
    protected_implementation = root / ".ai-dev" / "project.yaml"
    design_document.parent.mkdir(parents=True)
    protected_document.parent.mkdir(parents=True)
    implementation.parent.mkdir(parents=True)
    protected_implementation.parent.mkdir(parents=True)
    design_document.write_text(
        "# 公開設計\n\n```markdown\n## コード例\n```\n\n## 実在する節 ##\n",
        encoding="utf-8",
    )
    protected_document.write_text("# 内部基準\n", encoding="utf-8")
    implementation.write_text("value = 1\n", encoding="utf-8")
    protected_implementation.write_text("project: test\n", encoding="utf-8")
    _run_git(root, "add", ".")
    _run_git(root, "commit", "-m", "add design documents")
    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    ).stdout.strip()
    design_document.write_text("# 未コミットの見出し\n", encoding="utf-8")
    implementation.unlink()

    candidates = collect_design_reference_candidates(
        root,
        protected_patterns=["docs/quality/**"],
        commit_sha=commit_sha,
    )

    assert candidates == [
        "docs/design/current.md#公開設計",
        "docs/design/current.md#実在する節",
    ]
    implementation_candidates = collect_implementation_reference_candidates(
        root,
        ["src/app.py", ".ai-dev/project.yaml", "src/missing.py"],
        protected_patterns=[".ai-dev/**"],
        commit_sha=commit_sha,
    )
    assert implementation_candidates == ["src/app.py"]


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
        executed_test_cases=[
            ExecutedTestCase(
                id="tests/test_mock.py::test_required_behavior",
                node_id="tests/test_mock.py::test_required_behavior",
                file="tests/test_mock.py",
                status="PASS",
                evidence_reference="junit:trusted:required-behavior",
            )
        ],
        overall_status=VerificationStatus.PASS,
        started_at=now,
        finished_at=now,
        commit_sha=commit_sha,
    )


def test_quality_gate_rejects_untrusted_verification_target_variants() -> None:
    result = _trusted_result("a" * 40, ["src/app.py"], "diff")
    with pytest.raises(ValueError, match="did not pass"):
        _assert_verification_target(
            result.model_copy(update={"overall_status": VerificationStatus.FAIL}),
            commit_sha="a" * 40,
            changed_files=["src/app.py"],
        )
    with pytest.raises(ValueError, match="commit does not match"):
        _assert_verification_target(
            result,
            commit_sha="c" * 40,
            changed_files=["src/app.py"],
        )
    with pytest.raises(ValueError, match="files do not match"):
        _assert_verification_target(
            result,
            commit_sha="a" * 40,
            changed_files=["src/other.py"],
        )
    with pytest.raises(ValueError, match="failed or missing"):
        _assert_verification_target(
            result.model_copy(update={"results": []}),
            commit_sha="a" * 40,
            changed_files=["src/app.py"],
        )


def _traceability_result(
    *,
    reported_files: list[str],
    implementation_reference: str,
    design_reference: str = "docs/design/traceability.md#要件対応",
) -> AgentResult:
    developer = DeveloperResult(
        decision=Decision.PASS,
        summary="Traceability mappings collected.",
        changed_files=reported_files,
        requirement_implementations=[
            RequirementImplementationReference(
                requirement_id="BR-001",
                design_references=[design_reference],
                implementation_references=[implementation_reference],
            )
        ],
        acceptance_criterion_test_mappings=[
            AcceptanceCriterionTestMapping(
                requirement_id="BR-001",
                acceptance_criterion="Trusted verification passes",
                test_case_ids=["tests/test_mock.py::test_required_behavior"],
            )
        ],
    )
    return AgentResult(
        status=AgentRunStatus.SUCCESS,
        output=developer.model_dump(mode="json"),
    )


def _prepared_traceability_task(
    initialized_project: Path,
    *,
    changed_files: list[str] | None = None,
) -> tuple[LoadedConfig, SQLiteStateStore, TaskRecord, VerificationResult]:
    changed_files = ["src/app.py"] if changed_files is None else changed_files
    loaded = load_config(initialized_project)
    store = SQLiteStateStore(
        initialized_project / ".ai-dev" / "local" / "traceability-normalization.sqlite3"
    )
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
    gateway.changed_files[7] = [ChangedFile(path=path, status="modified") for path in changed_files]
    (initialized_project / "src").mkdir(exist_ok=True)
    (initialized_project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    requirement_items = parse_structured_issue_requirements(
        str(gateway.issues[41]["body"]), source_reference="mock://issues/41"
    )
    gateway.add_issue_comment(
        41,
        f"ai-dev 要件承認: 承認\n要件ダイジェスト: {requirements_digest(requirement_items)}",
    )
    verification = _trusted_result("a" * 40, changed_files, "reviewed diff")
    task = prepare_quality_task(
        store,
        gateway,
        initialized_project,
        issue_number=41,
        pull_request_number=7,
        stage=WorkflowState.SYSTEM_REVIEW,
        verification=verification,
    )
    return loaded, store, task, verification


def test_traceability_collection_normalizes_agent_files_from_host_verification(
    initialized_project: Path,
) -> None:
    loaded, store, task, verification = _prepared_traceability_task(initialized_project)
    provider = MockAgentProvider(
        scripted_results=[
            _traceability_result(
                reported_files=["src/agent-reported.py"],
                implementation_reference="src/app.py",
            )
        ]
    )

    updated = asyncio.run(
        _collect_host_validated_traceability(
            loaded,
            provider,
            store,
            initialized_project,
            task,
            verification,
        )
    )

    assert updated.evidence.developer_results[-1].changed_files == ["src/app.py"]
    assert updated.evidence.traceability[0].implementation_references == ["file:src/app.py"]
    traceability_request = provider.requests[0]
    assert "docs/<repository-relative-document>.md#<existing-section-heading>" in (
        traceability_request.prompt
    )
    reference_contract = traceability_request.context["reference_contract"]
    design_contract = reference_contract["design_references"]
    assert design_contract["required_format"] == (
        "docs/<repository-relative-document>.md#<existing-section-heading>"
    )
    assert design_contract["example"] == "docs/design/traceability.md#要件対応"
    assert design_contract["file_path_only_is_invalid"] is True
    assert "docs/design/traceability.md#要件対応" in design_contract["allowed_values"]
    assert reference_contract["implementation_references"] == {"allowed_values": ["src/app.py"]}
    assert reference_contract["test_case_ids"] == {
        "allowed_values": ["tests/test_mock.py::test_required_behavior"]
    }


@pytest.mark.parametrize(
    "design_reference",
    [
        "docs/quality/protected.md#内部基準",
        "docs/design/traceability.md#存在しない節",
    ],
)
def test_traceability_collection_rejects_design_references_outside_host_candidates(
    initialized_project: Path,
    design_reference: str,
) -> None:
    protected_document = initialized_project / "docs" / "quality" / "protected.md"
    protected_document.parent.mkdir(parents=True, exist_ok=True)
    protected_document.write_text("# 保護文書\n\n## 内部基準\n", encoding="utf-8")
    loaded, store, task, verification = _prepared_traceability_task(initialized_project)
    provider = MockAgentProvider(
        scripted_results=[
            _traceability_result(
                reported_files=["src/agent-reported.py"],
                implementation_reference="src/app.py",
                design_reference=design_reference,
            )
        ]
    )

    with pytest.raises(ValueError, match="host-approved candidate set"):
        asyncio.run(
            _collect_host_validated_traceability(
                loaded,
                provider,
                store,
                initialized_project,
                task,
                verification,
            )
        )
    allowed_values = provider.requests[0].context["reference_contract"]["design_references"][
        "allowed_values"
    ]
    assert "docs/quality/protected.md#内部基準" not in allowed_values


def test_traceability_collection_still_rejects_unverified_agent_references(
    initialized_project: Path,
) -> None:
    loaded, store, task, verification = _prepared_traceability_task(initialized_project)
    (initialized_project / "src" / "unverified.py").write_text("value = 2\n", encoding="utf-8")
    provider = MockAgentProvider(
        scripted_results=[
            _traceability_result(
                reported_files=["src/agent-reported.py"],
                implementation_reference="src/unverified.py",
            )
        ]
    )

    with pytest.raises(ValueError, match=r"implementation reference.*host-approved candidate set"):
        asyncio.run(
            _collect_host_validated_traceability(
                loaded,
                provider,
                store,
                initialized_project,
                task,
                verification,
            )
        )


def test_traceability_collection_excludes_protected_changed_implementation_references(
    initialized_project: Path,
) -> None:
    loaded, store, task, verification = _prepared_traceability_task(
        initialized_project,
        changed_files=["src/app.py", ".ai-dev/project.yaml"],
    )
    provider = MockAgentProvider(
        scripted_results=[
            _traceability_result(
                reported_files=["src/app.py", ".ai-dev/project.yaml"],
                implementation_reference=".ai-dev/project.yaml",
            )
        ]
    )

    with pytest.raises(ValueError, match=r"implementation reference.*host-approved candidate set"):
        asyncio.run(
            _collect_host_validated_traceability(
                loaded,
                provider,
                store,
                initialized_project,
                task,
                verification,
            )
        )
    traceability_request = provider.requests[0]
    assert traceability_request.context["changed_files"] == ["src/app.py"]
    assert traceability_request.context["reference_contract"]["implementation_references"] == {
        "allowed_values": ["src/app.py"]
    }


def test_traceability_collection_requires_unprotected_implementation_candidate(
    initialized_project: Path,
) -> None:
    loaded, store, task, verification = _prepared_traceability_task(
        initialized_project,
        changed_files=[".ai-dev/project.yaml"],
    )
    provider = MockAgentProvider()

    with pytest.raises(ValueError, match="no unprotected implementation reference candidates"):
        asyncio.run(
            _collect_host_validated_traceability(
                loaded,
                provider,
                store,
                initialized_project,
                task,
                verification,
            )
        )
    assert provider.requests == []


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


def test_local_verification_isolates_provider_credentials_and_github_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _git_project(tmp_path)
    inherited_only = (
        "ANTHROPIC_API_KEY",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_ACTIONS",
        "GITHUB_EVENT_PATH",
        "AI_DEV_TRUSTED_EVENT",
        "VERIFICATION_UNKNOWN_VALUE",
    )
    for name in inherited_only:
        monkeypatch.setenv(name, "configured-outside-verification")
    monkeypatch.setenv("AI_DEV_PROVIDER", "claude")
    monkeypatch.setenv("AI_DEV_GITHUB_GATEWAY", "gh")
    child_assertions = (
        "assert (environment := __import__('os').environ) "
        f"and all(name not in environment for name in {inherited_only!r}) "
        "and environment['AI_DEV_PROVIDER'] == 'mock' "
        "and environment['AI_DEV_GITHUB_GATEWAY'] == 'mock' "
        "and environment.get('PATH')"
    )
    policy = VerificationPolicy(
        commands=[
            VerificationCommand(
                name="isolated-environment",
                argv=[sys.executable, "-c", child_assertions],
                timeout_seconds=30,
            )
        ],
        secret_scan=False,
    )

    result = LocalVerificationRunner().run(root, ["app.py"], policy)

    assert result.overall_status == VerificationStatus.PASS
    assert result.results[0].status == RunStatus.PASS
    assert os.environ["AI_DEV_PROVIDER"] == "claude"
    assert os.environ["ANTHROPIC_API_KEY"] == "configured-outside-verification"


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
    (initialized_project / "src").mkdir(exist_ok=True)
    (initialized_project / "src" / "app.py").write_text("value = 1\n", encoding="utf-8")
    requirement_items = parse_structured_issue_requirements(
        str(gateway.issues[41]["body"]), source_reference="mock://issues/41"
    )
    gateway.add_issue_comment(
        41,
        f"ai-dev 要件承認: 承認\n要件ダイジェスト: {requirements_digest(requirement_items)}",
    )
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
        "developer",
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
    changed_body = str(gateway.issues[41]["body"]).replace(
        "Trusted verification passes",
        "Trusted verification and traceability pass",
    )
    gateway.update_issue(41, changed_body)
    changed_requirements = parse_structured_issue_requirements(
        changed_body, source_reference="mock://issues/41"
    )
    gateway.add_issue_comment(
        41,
        f"ai-dev 要件承認: 承認\n要件ダイジェスト: {requirements_digest(changed_requirements)}",
    )
    with pytest.raises(ValueError, match="another requirements digest"):
        prepare_quality_task(
            store,
            gateway,
            initialized_project,
            issue_number=41,
            pull_request_number=7,
            stage=WorkflowState.QA_ASSESSMENT,
            verification=verification,
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
