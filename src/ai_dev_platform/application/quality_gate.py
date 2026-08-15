"""Ordered CI quality gates for one exact Pull Request head SHA."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from ai_dev_platform.application.ci_evidence import collect_required_ci_evidence
from ai_dev_platform.application.quality_artifacts import (
    build_quality_artifact,
    read_quality_artifact,
    write_quality_artifact,
)
from ai_dev_platform.application.requirements import (
    find_requirements_approval,
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.application.traceability import (
    assert_references_exist_at_commit,
    build_validated_traceability,
    collect_design_reference_candidates,
    collect_implementation_reference_candidates,
)
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import LoadedConfig
from ai_dev_platform.domain.models import (
    AgentRequest,
    AgentResult,
    AgentRunStatus,
    Decision,
    DeveloperResult,
    EvidenceReference,
    GitHubCheckRunEvidence,
    IssueData,
    RequirementItem,
    RequirementsApproval,
    RequirementsResult,
    ReviewType,
    TaskEvidence,
    TaskRecord,
    TraceabilityRecord,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from ai_dev_platform.infrastructure.git import GitOperationError, GitWorktreeGateway
from ai_dev_platform.infrastructure.github import GitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore, TaskNotFoundError
from ai_dev_platform.providers.base import AgentProvider
from ai_dev_platform.security.scanner import scan_tree

_SAFE_PROVIDER_ERROR_CODE = re.compile(r"[A-Za-z0-9_.:-]{1,100}")


def _developer_traceability_failure_message(result: AgentResult) -> str:
    """Return a diagnostic that cannot expose provider response or exception text."""
    error_code = result.error_code or "provider_failure"
    if _SAFE_PROVIDER_ERROR_CODE.fullmatch(error_code) is None:
        error_code = "provider_failure"
    return (
        f"developer traceability collection failed: status={result.status.value}; code={error_code}"
    )


def _quality_gate_failure_message(
    store: SQLiteStateStore,
    task: TaskRecord,
    stage: WorkflowState,
) -> str:
    """Return the latest safe transition reason without exposing provider details."""
    failure_code = "quality_gate_failed"
    for event in reversed(store.list_events(task.task_id)):
        details = event.get("details")
        if (
            event.get("action") != "state_transition"
            or not isinstance(details, dict)
            or details.get("from") != stage.value
        ):
            continue
        candidate = event.get("result")
        if isinstance(candidate, str) and _SAFE_PROVIDER_ERROR_CODE.fullmatch(candidate):
            failure_code = candidate
        break
    return (
        f"{stage.value} did not pass its ordered quality gate: "
        f"state={task.state.value}; code={failure_code}"
    )


def _bootstrap_evidence(
    issue: IssueData,
    verification: VerificationResult,
    ci_check_results: list[GitHubCheckRunEvidence],
    requirement_items: list[RequirementItem],
    approval: RequirementsApproval,
) -> TaskEvidence:
    issue_reference = EvidenceReference(
        id="github-issue-requirements",
        kind="github",
        reference="github:issue-body",
        safe_summary="対象Issueから取得した承認済み要件です。",
    )
    requirements = RequirementsResult(
        decision=Decision.PASS,
        summary="対象Issueの構造化要件と人間によるdigest承認を確認しました。",
        evidence=[issue_reference],
        requirements=requirement_items,
        business_requirements=[
            item.description for item in requirement_items if item.type == "BUSINESS"
        ],
        acceptance_criteria=[
            criterion for item in requirement_items for criterion in item.acceptance_criteria
        ],
        scope=["対象IssueおよびPull Request"],
        requirements_source="STRUCTURED_ISSUE",
        human_approved=True,
    )
    return TaskEvidence(
        requirements_result=requirements,
        requirements_approval=approval,
        trusted_verification_results=[verification],
        trusted_ci_results=list(ci_check_results),
        traceability=[
            TraceabilityRecord(requirement_id=requirement.id) for requirement in requirement_items
        ],
    )


def _assert_verification_target(
    verification: VerificationResult,
    *,
    commit_sha: str,
    changed_files: list[str],
) -> None:
    if verification.overall_status != VerificationStatus.PASS:
        raise ValueError("trusted verification did not pass")
    if verification.commit_sha != commit_sha:
        raise ValueError("trusted verification commit does not match Pull Request head")
    if sorted(verification.changed_files) != sorted(changed_files):
        raise ValueError("trusted verification files do not match Pull Request files")
    if not verification.results or any(
        result.required and result.status.value != "PASS" for result in verification.results
    ):
        raise ValueError("trusted verification contains a failed or missing required result")


def _validated_ci_results(
    results: list[GitHubCheckRunEvidence],
    *,
    commit_sha: str,
    required_names: list[str],
) -> list[GitHubCheckRunEvidence]:
    """Return exactly one latest successful Check Run for every required name."""
    if len(required_names) != len(set(required_names)):
        raise ValueError("required CI check names must be unique")
    latest: dict[str, GitHubCheckRunEvidence] = {}
    for result in results:
        if result.commit_sha != commit_sha:
            raise ValueError("trusted CI evidence belongs to another commit")
        current = latest.get(result.name)
        if current is None or result.check_run_id > current.check_run_id:
            latest[result.name] = result
    if any(name not in latest for name in required_names):
        raise ValueError("trusted CI evidence is missing a required check")
    selected = [latest[name] for name in required_names]
    if any(
        result.status != "completed"
        or result.conclusion != "success"
        or result.details_url is None
        or result.completed_at is None
        for result in selected
    ):
        raise ValueError("trusted CI evidence contains an incomplete or failed check")
    return selected


def prepare_quality_task(
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    stage: WorkflowState,
    verification: VerificationResult,
    git: GitWorktreeGateway | None = None,
    ci_check_results: list[GitHubCheckRunEvidence] | None = None,
    required_ci_check_names: list[str] | None = None,
) -> TaskRecord:
    """Collect exact targets and accept only trusted, SHA-bound host verification."""
    issue = github.get_issue(issue_number)
    requirement_items = parse_structured_issue_requirements(
        issue.body,
        source_reference=issue.url or f"github:issue:{issue.number}",
    )
    requirements_approval = find_requirements_approval(
        issue.number,
        requirement_items,
        github.get_issue_comments(issue.number),
    )
    if requirements_approval is None:
        raise ValueError("formal requirements approval is missing or stale")
    pull_request = github.get_pull_request(pull_request_number)
    if pull_request.base_branch == pull_request.head_branch:
        raise ValueError("Pull Request head and base branches must differ")
    if scan_tree(root):
        raise ValueError("quality gate blocked because secret-like content was detected")
    try:
        changed_files = (
            git.changed_files_between(verification.base_commit_sha, pull_request.head_sha)
            if git is not None
            else [item.path for item in github.get_changed_files(pull_request_number)]
        )
    except GitOperationError as exc:
        raise ValueError("trusted commit range collection failed") from exc
    _assert_verification_target(
        verification,
        commit_sha=pull_request.head_sha,
        changed_files=changed_files,
    )
    required_names = required_ci_check_names or ["quality (3.12)", "quality (3.13)"]
    supplied_ci_results = (
        ci_check_results
        if ci_check_results is not None
        else github.get_commit_check_runs(pull_request.head_sha)
    )
    validated_ci_results = _validated_ci_results(
        supplied_ci_results,
        commit_sha=pull_request.head_sha,
        required_names=required_names,
    )
    try:
        task = store.get_task_by_issue(issue_number)
    except TaskNotFoundError:
        if stage != WorkflowState.SYSTEM_REVIEW:
            raise ValueError("System Review must create the ordered quality-gate state") from None
        task = store.create_task(
            TaskRecord(
                task_id=f"issue-{issue_number}",
                issue_number=issue_number,
                state=stage,
                commit_sha=pull_request.head_sha,
                branch=pull_request.head_branch,
                pull_request_number=pull_request_number,
                context={
                    "issue_reference": {
                        "number": issue.number,
                        "url": issue.url,
                        "labels": issue.labels,
                    }
                },
                evidence=_bootstrap_evidence(
                    issue,
                    verification,
                    validated_ci_results,
                    requirement_items,
                    requirements_approval,
                ),
            )
        )
    else:
        persisted_requirements = task.evidence.requirements_result
        persisted_approval = task.evidence.requirements_approval
        current_digest = requirements_digest(requirement_items)
        if (
            persisted_requirements is None
            or persisted_approval is None
            or requirements_digest(persisted_requirements.requirements) != current_digest
            or persisted_approval.requirements_digest != current_digest
            or requirements_approval.requirements_digest != current_digest
        ):
            raise ValueError("persisted quality state belongs to another requirements digest")
        if task.pull_request_number != pull_request_number:
            raise ValueError("persisted quality state belongs to another Pull Request")
        if task.commit_sha != pull_request.head_sha:
            raise ValueError("persisted quality state belongs to another commit SHA")
        if task.state != stage:
            raise ValueError("persisted task is not ready for the requested ordered gate")
        latest = task.evidence.trusted_verification_results[-1]
        _assert_verification_target(
            latest,
            commit_sha=pull_request.head_sha,
            changed_files=changed_files,
        )
        _validated_ci_results(
            task.evidence.trusted_ci_results,
            commit_sha=pull_request.head_sha,
            required_names=required_names,
        )
    return task


async def _collect_host_validated_traceability(
    loaded: LoadedConfig,
    provider: AgentProvider,
    store: SQLiteStateStore,
    root: Path,
    task: TaskRecord,
    verification: VerificationResult,
) -> TaskRecord:
    """Ask the developer role for mappings, then trust only host-validated references."""
    requirements = task.evidence.requirements_result
    definition = loaded.agents.get("developer")
    if requirements is None or definition is None:
        raise ValueError("developer traceability collection is not configured")
    design_reference_candidates = collect_design_reference_candidates(
        root,
        protected_patterns=loaded.project.protected_paths,
        commit_sha=task.commit_sha,
    )
    if not design_reference_candidates:
        raise ValueError("no unprotected design reference candidates were found")
    implementation_reference_candidates = collect_implementation_reference_candidates(
        root,
        verification.changed_files,
        protected_patterns=loaded.project.protected_paths,
        commit_sha=task.commit_sha,
    )
    if not implementation_reference_candidates:
        raise ValueError("no unprotected implementation reference candidates were found")
    request = AgentRequest(
        agent_id=definition.id,
        prompt=(
            "承認済み要件について、既存の設計文書・対象commitの実装ファイル・"
            "ホスト実行済みテストケースの対応だけを報告してください。ファイルは変更しません。"
            "design_referencesは必ず"
            "`docs/<repository-relative-document>.md#<existing-section-heading>`形式とし、"
            "`#`と空でない既存の節見出しを含めてください。"
            "例: `docs/design/traceability.md#要件対応`。"
            "節のないファイルパスだけの設計参照は不正です。"
            "design_referencesはreference_contractにあるallowed_valuesからだけ選んでください。"
            "implementation_referencesもreference_contractにあるallowed_valuesからだけ"
            "選んでください。"
        ),
        system_prompt=definition.system_prompt,
        context={
            "issue_number": task.issue_number,
            "commit_sha": task.commit_sha,
            "requirements": [item.model_dump(mode="json") for item in requirements.requirements],
            "changed_files": implementation_reference_candidates,
            "verified_test_cases": [
                item.model_dump(mode="json") for item in verification.executed_test_cases
            ],
            "reference_contract": {
                "design_references": {
                    "required_format": (
                        "docs/<repository-relative-document>.md#<existing-section-heading>"
                    ),
                    "example": "docs/design/traceability.md#要件対応",
                    "file_path_only_is_invalid": True,
                    "allowed_values": design_reference_candidates,
                },
                "implementation_references": {
                    "allowed_values": implementation_reference_candidates,
                },
                "test_case_ids": {
                    "allowed_values": [item.id for item in verification.executed_test_cases],
                },
            },
            "traceability_collection_only": True,
        },
        model=definition.model,
        max_turns=min(definition.max_turns, loaded.project.workflow.max_agent_turns),
        timeout_seconds=loaded.project.workflow.timeout_minutes * 60,
        max_budget_usd=loaded.project.budget.per_task.stop_usd,
        allowed_tools=[
            tool for tool in definition.available_tools if tool in {"Read", "Glob", "Grep"}
        ],
        forbidden_tools=list(dict.fromkeys([*definition.forbidden_tools, "Write", "Edit"])),
        working_directory=str(root.resolve()),
        readable_paths=definition.readable_paths,
        writable_paths=[],
        protected_paths=definition.protected_paths,
        internet_access=definition.internet_access,
        output_schema=DeveloperResult.model_json_schema(),
    )
    provider_result = await provider.execute(request)
    if provider_result.status != AgentRunStatus.SUCCESS:
        raise ValueError(_developer_traceability_failure_message(provider_result))
    developer_result = DeveloperResult.model_validate(provider_result.output)
    allowed_design_references = set(design_reference_candidates)
    if any(
        reference not in allowed_design_references
        for mapping in developer_result.requirement_implementations
        for reference in mapping.design_references
    ):
        raise ValueError("design reference is not in the host-approved candidate set")
    allowed_implementation_references = set(implementation_reference_candidates)
    if any(
        reference not in allowed_implementation_references
        for mapping in developer_result.requirement_implementations
        for reference in mapping.implementation_references
    ):
        raise ValueError("implementation reference is not in the host-approved candidate set")
    developer_result = developer_result.model_copy(
        update={"changed_files": list(verification.changed_files)}
    )
    traces = build_validated_traceability(
        root.resolve(),
        requirements,
        developer_result,
        verification,
        protected_patterns=loaded.project.protected_paths,
        protected_path_approved=False,
    )
    assert_references_exist_at_commit(root.resolve(), traces, task.commit_sha)
    evidence = task.evidence.model_copy(deep=True)
    evidence.developer_results.append(developer_result)
    evidence.agent_reported_test_results.extend(developer_result.test_results)
    evidence.traceability = traces
    updated = store.save_task(task.model_copy(update={"evidence": evidence}))
    store.append_event(
        task.task_id,
        "developer",
        "traceability_collected",
        "success",
        {
            "commit_sha": task.commit_sha,
            "requirement_count": len(traces),
            "verification_run_id": verification.run_id,
        },
    )
    return updated


def run_quality_gate(
    loaded: LoadedConfig,
    provider: AgentProvider,
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    stage: WorkflowState,
    verification: VerificationResult,
    git: GitWorktreeGateway | None = None,
    ci_check_results: list[GitHubCheckRunEvidence] | None = None,
) -> TaskRecord:
    """Execute one ordered stage and publish its formal comment and unique Check."""
    verification_policy = loaded.verification
    if verification_policy is None:
        raise ValueError("host verification policy is not configured")
    if ci_check_results is None:
        ci_check_results = collect_required_ci_evidence(
            github,
            verification.commit_sha or "",
            verification_policy.required_ci_checks,
            timeout_seconds=verification_policy.ci_wait_timeout_seconds,
        ).checks
    task = prepare_quality_task(
        store,
        github,
        root,
        issue_number=issue_number,
        pull_request_number=pull_request_number,
        stage=stage,
        verification=verification,
        git=git,
        ci_check_results=ci_check_results,
        required_ci_check_names=verification_policy.required_ci_checks,
    )
    if stage == WorkflowState.SYSTEM_REVIEW and not task.evidence.developer_results:
        task = asyncio.run(
            _collect_host_validated_traceability(
                loaded,
                provider,
                store,
                root,
                task,
                verification,
            )
        )
    runner = WorkflowRunner(
        loaded.project,
        loaded.agents,
        provider,
        store,
        root=root,
        github=github,
        git=git,
    )
    return asyncio.run(runner.run_one_stage(task.task_id, stage))


def run_integrated_quality_gates(
    loaded: LoadedConfig,
    provider: AgentProvider,
    store: SQLiteStateStore,
    github: GitHubGateway,
    root: Path,
    *,
    issue_number: int,
    pull_request_number: int,
    verification: VerificationResult,
    artifact_directory: Path,
    git: GitWorktreeGateway | None = None,
    ci_check_results: list[GitHubCheckRunEvidence] | None = None,
) -> TaskRecord:
    """Run System, Business, and QA once each in one process and verify each JSON handoff."""
    verification_policy = loaded.verification
    if verification_policy is None:
        raise ValueError("host verification policy is not configured")
    if ci_check_results is None:
        ci_check_results = collect_required_ci_evidence(
            github,
            verification.commit_sha or "",
            verification_policy.required_ci_checks,
            timeout_seconds=verification_policy.ci_wait_timeout_seconds,
        ).checks
    stages = (
        (WorkflowState.SYSTEM_REVIEW, ReviewType.SYSTEM, "system-review.json"),
        (WorkflowState.BUSINESS_REVIEW, ReviewType.BUSINESS, "business-review.json"),
        (WorkflowState.QA_ASSESSMENT, ReviewType.QA, "qa-assessment.json"),
    )
    task: TaskRecord | None = None
    for workflow_stage, review_type, filename in stages:
        task = run_quality_gate(
            loaded,
            provider,
            store,
            github,
            root,
            issue_number=issue_number,
            pull_request_number=pull_request_number,
            stage=workflow_stage,
            verification=verification,
            git=git,
            ci_check_results=ci_check_results,
        )
        expected_next = {
            WorkflowState.SYSTEM_REVIEW: WorkflowState.BUSINESS_REVIEW,
            WorkflowState.BUSINESS_REVIEW: WorkflowState.QA_ASSESSMENT,
            WorkflowState.QA_ASSESSMENT: WorkflowState.HUMAN_APPROVAL_REQUIRED,
        }[workflow_stage]
        if task.state != expected_next:
            raise ValueError(_quality_gate_failure_message(store, task, workflow_stage))
        artifact_path = write_quality_artifact(
            artifact_directory / filename,
            build_quality_artifact(task, review_type),
        )
        read_quality_artifact(
            artifact_path,
            expected_stage=review_type,
            issue_number=issue_number,
            pull_request_number=pull_request_number,
            commit_sha=verification.commit_sha or "",
        )
    assert task is not None
    return task
