"""Stage-specific, redacted context collection for agent requests."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ai_dev_platform.domain.models import AgentDefinition, FindingStatus, TaskRecord, WorkflowState
from ai_dev_platform.infrastructure.git import GitOperationError, GitWorktreeGateway
from ai_dev_platform.infrastructure.github import GitHubError, GitHubGateway
from ai_dev_platform.security.paths import assert_read_allowed
from ai_dev_platform.security.scanner import SensitiveContentError, ensure_safe_to_persist

_SENSITIVE_KEY = re.compile(
    r"^(?:secret|token|password|credential|private[_-]?key|production[_-]?data)"
    r"(?:_value|_content|_raw)?$",
    re.I,
)


class ContextCollectionError(RuntimeError):
    """Sanitized context collection failure with a stable workflow code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _unique_results(values: list[Any]) -> list[Any]:
    """Remove legacy duplicate stage results while preserving first-seen order."""
    seen: set[str] = set()
    unique: list[Any] = []
    for value in values:
        run_id = getattr(value, "run_id", "")
        if run_id and run_id in seen:
            continue
        if run_id:
            seen.add(run_id)
        unique.append(value)
    return unique


def sanitize_agent_context(value: Any, *, key: str = "") -> Any:
    """Recursively reject secret-bearing keys and values before provider transmission."""
    if key and _SENSITIVE_KEY.search(key):
        raise SensitiveContentError("sensitive context field was rejected")
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_agent_context(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_agent_context(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [sanitize_agent_context(item, key=key) for item in value]
    if isinstance(value, str):
        ensure_safe_to_persist(value)
    return value


class TaskContextBuilder:
    """Collect the minimum approved context required by each role."""

    def __init__(
        self,
        root: Path,
        *,
        github: GitHubGateway | None = None,
        git: GitWorktreeGateway | None = None,
    ) -> None:
        self.root = root.resolve()
        self.github = github
        self.git = git

    def _safe_file(self, relative: str) -> str:
        path = self.root / relative
        if not path.exists() or not path.is_file():
            return ""
        assert_read_allowed(self.root, path)
        if path.stat().st_size > 500_000:
            return "[omitted: file exceeds context size limit]"
        text = path.read_text(encoding="utf-8")
        ensure_safe_to_persist(text)
        return text

    def _repository_structure(self) -> list[str]:
        values: list[str] = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(self.root)
            if any(
                part in {".git", ".venv", "__pycache__", "local", "dist", "build"}
                or part.startswith(".uv-cache")
                for part in relative.parts
            ):
                continue
            try:
                assert_read_allowed(self.root, path)
            except PermissionError:
                continue
            values.append(relative.as_posix())
            if len(values) >= 500:
                values.append("[truncated]")
                break
        return values

    def build(
        self,
        task: TaskRecord,
        stage: WorkflowState,
        agent: AgentDefinition,
    ) -> dict[str, Any]:
        """Build and validate the common and role-specific context."""
        issue = self.github.get_issue(task.issue_number) if self.github is not None else None
        issue_context = (
            issue.model_dump(mode="json")
            if issue is not None
            else task.context.get(
                "issue",
                {
                    "number": task.issue_number,
                    "title": task.context.get("issue_title", ""),
                    "body": task.context.get("issue_body", ""),
                    "labels": task.context.get("issue_labels", []),
                },
            )
        )
        requirements = task.evidence.requirements_result
        deployment = task.evidence.deployment_configuration
        context: dict[str, Any] = {
            "issue_number": task.issue_number,
            "issue_title": issue_context.get("title", ""),
            "issue_body": issue_context.get("body", ""),
            "issue_url": issue_context.get("url", ""),
            "business_requirements": (
                requirements.business_requirements if requirements is not None else []
            ),
            "requirements": (
                [item.model_dump(mode="json") for item in requirements.requirements]
                if requirements is not None
                else []
            ),
            "acceptance_criteria": (
                requirements.acceptance_criteria if requirements is not None else []
            ),
            "scope": requirements.scope if requirements is not None else [],
            "out_of_scope": requirements.out_of_scope if requirements is not None else [],
            "human_decisions": (requirements.human_decisions if requirements is not None else []),
            "commit_sha": task.commit_sha,
            "branch": task.branch,
            "pull_request_number": task.pull_request_number,
            "iteration": task.iteration,
            "protection_policy": {
                "writable_paths": agent.writable_paths,
                "protected_paths": agent.protected_paths,
                "forbidden_actions": agent.forbidden_actions,
                "forbidden_commands": agent.forbidden_commands,
            },
            "agent_definition": agent.model_dump(mode="json"),
            "deployment_configuration": (
                deployment.model_dump(mode="json") if deployment is not None else None
            ),
            "available_data_classifications": task.context.get(
                "available_data_classifications", ["PUBLIC_DUMMY", "SYNTHETIC"]
            ),
        }
        if stage in {
            WorkflowState.PLANNING,
            WorkflowState.DESIGNING,
            WorkflowState.IMPLEMENTING,
        }:
            context.update(
                {
                    "current_design": self._safe_file("docs/architecture.md"),
                    "repository_structure": self._repository_structure(),
                    "changeable_paths": agent.writable_paths,
                    "protected_paths": agent.protected_paths,
                    "previous_findings": [
                        finding.model_dump(mode="json")
                        for finding in task.evidence.unresolved_findings
                        if finding.status
                        not in {FindingStatus.RESOLVED, FindingStatus.ACCEPTED_BY_HUMAN}
                    ],
                    "rework_requirements": [
                        {
                            "finding_id": finding.id,
                            "severity": finding.severity,
                            "requirement_ids": finding.requirement_ids,
                            "file": finding.file,
                            "required_fix": finding.required_fix,
                            "acceptance_test": finding.acceptance_test,
                            "recurrence": finding.recurrence,
                        }
                        for finding in task.evidence.unresolved_findings
                        if finding.status
                        not in {FindingStatus.RESOLVED, FindingStatus.ACCEPTED_BY_HUMAN}
                    ],
                    "acceptance_tests": task.context.get("acceptance_tests", []),
                    "agent_reported_test_results": [
                        result.model_dump(mode="json")
                        for result in task.evidence.agent_reported_test_results
                    ],
                    "trusted_verification_results": [
                        result.model_dump(mode="json")
                        for result in task.evidence.trusted_verification_results
                    ],
                    "current_git_diff": self.git.diff() if self.git is not None else "",
                    "changed_files": self.git.changed_files() if self.git is not None else [],
                }
            )
        elif stage == WorkflowState.SYSTEM_REVIEW:
            context.update(self._review_context(task, include_business=False))
        elif stage == WorkflowState.BUSINESS_REVIEW:
            context.update(self._review_context(task, include_business=True))
        elif stage == WorkflowState.QA_ASSESSMENT:
            quality_evidence = task.evidence.model_dump(mode="json")
            quality_evidence["system_reviews"] = [
                item.model_dump(mode="json")
                for item in _unique_results(task.evidence.system_reviews)
            ]
            quality_evidence["business_reviews"] = [
                item.model_dump(mode="json")
                for item in _unique_results(task.evidence.business_reviews)
            ]
            context.update(
                {
                    "all_quality_evidence": quality_evidence,
                    "test_plan": task.context.get("test_plan", []),
                    "coverage": task.context.get("coverage", {}),
                    "bug_history": task.context.get("bug_history", []),
                    "environment_quality_gates": task.context.get("environment_quality_gates", []),
                }
            )
        context.update(task.context.get("approved_context", {}))
        sanitized = sanitize_agent_context(context)
        if not isinstance(sanitized, dict):
            raise ValueError("agent context must remain a mapping")
        ensure_safe_to_persist(json.dumps(sanitized, ensure_ascii=False, default=str))
        return sanitized

    def _review_context(self, task: TaskRecord, *, include_business: bool) -> dict[str, Any]:
        verification = (
            task.evidence.trusted_verification_results[-1]
            if task.evidence.trusted_verification_results
            else None
        )
        if self.git is not None and verification is not None:
            if verification.commit_sha != task.commit_sha:
                raise ContextCollectionError("verified_commit_diff_target_mismatch")
            try:
                pr_diff = self.git.verified_commit_diff(verification)
                changed_files = [
                    {
                        "path": path,
                        "status": "changed",
                        "additions": 0,
                        "deletions": 0,
                    }
                    for path in self.git.changed_files_between(
                        verification.base_commit_sha,
                        verification.commit_sha,
                    )
                ]
            except GitOperationError as exc:
                raise ContextCollectionError("verified_commit_diff_rejected") from exc
        elif self.github is not None and task.pull_request_number is not None:
            try:
                pr_diff = self.github.get_pull_request_diff(task.pull_request_number)
                changed_files = [
                    item.model_dump(mode="json")
                    for item in self.github.get_changed_files(task.pull_request_number)
                ]
            except GitHubError as exc:
                raise ContextCollectionError("github_pull_request_context_unavailable") from exc
        else:
            pr_diff = task.context.get("pull_request_diff", "")
            changed_files = task.context.get("changed_files", [])
        result: dict[str, Any] = {
            "pull_request_diff": pr_diff,
            "changed_files": changed_files,
            "design": self._safe_file("docs/architecture.md"),
            "traceability": [item.model_dump(mode="json") for item in task.evidence.traceability],
            "trusted_verification_results": [
                item.model_dump(mode="json") for item in task.evidence.trusted_verification_results
            ],
            "trusted_ci_results": [
                item.model_dump(mode="json") for item in task.evidence.trusted_ci_results
            ],
            "static_analysis_results": task.context.get("static_analysis_results", []),
            "secret_scan_results": task.context.get("secret_scan_results", []),
            "dependency_scan_results": task.context.get("dependency_scan_results", []),
            "previous_findings": [
                item.model_dump(mode="json") for item in task.evidence.unresolved_findings
            ],
        }
        if include_business:
            result.update(
                {
                    "business_rules": self._safe_file("docs/business-rules/rules.md"),
                    "input_examples": task.context.get("input_examples", []),
                    "processing_results": task.context.get("processing_results", []),
                    "fixed_evaluation_cases": task.context.get("fixed_evaluation_cases", []),
                    "expected_results": task.context.get("expected_results", []),
                    "external_sources": task.context.get("external_sources", []),
                    "source_data_as_of": task.context.get("source_data_as_of"),
                }
            )
        return result
