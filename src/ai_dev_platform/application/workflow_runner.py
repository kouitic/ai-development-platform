"""LangGraph execution with deterministic verification and evidence gates."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, TypedDict, TypeVar

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ai_dev_platform.application.context_builder import TaskContextBuilder
from ai_dev_platform.application.requirements import (
    find_requirements_approval,
    parse_structured_issue_requirements,
    requirements_digest,
)
from ai_dev_platform.application.traceability import (
    assert_references_exist_at_commit,
    build_validated_traceability,
    review_coverage_failure,
    traceability_failure,
)
from ai_dev_platform.domain.models import (
    AgentDefinition,
    AgentRequest,
    AgentRunStatus,
    BusinessReviewResult,
    Decision,
    DeveloperResult,
    Finding,
    FindingSeverity,
    FindingStatus,
    ProjectConfig,
    QaAssessmentResult,
    RequirementsResult,
    ReviewType,
    StageResult,
    SystemReviewResult,
    TaskEvidence,
    TaskRecord,
    TraceabilityRecord,
    VerificationPolicy,
    VerificationResult,
    VerificationStatus,
    WorkflowState,
)
from ai_dev_platform.domain.workflow import TERMINAL_STATES, assert_transition
from ai_dev_platform.infrastructure.git import (
    GitOperationError,
    GitWorktreeGateway,
    MockGitWorktree,
)
from ai_dev_platform.infrastructure.github import GitHubError, GitHubGateway, MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.infrastructure.verification import VerificationError, VerificationRunner
from ai_dev_platform.providers.base import AgentProvider
from ai_dev_platform.security.scanner import SensitiveContentError


class GraphState(TypedDict):
    """Minimal graph state; detailed state is transactionally stored in SQLite."""

    task_id: str
    state: str
    steps: int


STAGE_AGENT: Mapping[WorkflowState, str] = {
    WorkflowState.REQUIREMENTS_ANALYSIS: "conversation",
    WorkflowState.PLANNING: "developer",
    WorkflowState.DESIGNING: "developer",
    WorkflowState.IMPLEMENTING: "developer",
    WorkflowState.SYSTEM_REVIEW: "system-reviewer",
    WorkflowState.BUSINESS_REVIEW: "business-reviewer",
    WorkflowState.QA_ASSESSMENT: "qa",
}

PASS_NEXT: Mapping[WorkflowState, WorkflowState] = {
    WorkflowState.REQUIREMENTS_ANALYSIS: WorkflowState.DEPLOYMENT_CONFIGURATION,
    WorkflowState.PLANNING: WorkflowState.DESIGNING,
    WorkflowState.DESIGNING: WorkflowState.IMPLEMENTING,
    WorkflowState.IMPLEMENTING: WorkflowState.AUTOMATED_TESTING,
    WorkflowState.SYSTEM_REVIEW: WorkflowState.BUSINESS_REVIEW,
    WorkflowState.BUSINESS_REVIEW: WorkflowState.QA_ASSESSMENT,
    WorkflowState.QA_ASSESSMENT: WorkflowState.HUMAN_APPROVAL_REQUIRED,
}

RESULT_MODELS: Mapping[WorkflowState, type[StageResult]] = {
    WorkflowState.REQUIREMENTS_ANALYSIS: RequirementsResult,
    WorkflowState.PLANNING: DeveloperResult,
    WorkflowState.DESIGNING: DeveloperResult,
    WorkflowState.IMPLEMENTING: DeveloperResult,
    WorkflowState.SYSTEM_REVIEW: SystemReviewResult,
    WorkflowState.BUSINESS_REVIEW: BusinessReviewResult,
    WorkflowState.QA_ASSESSMENT: QaAssessmentResult,
}

CHECK_NAMES: Mapping[WorkflowState, str] = {
    WorkflowState.SYSTEM_REVIEW: "ai-quality/system-review",
    WorkflowState.BUSINESS_REVIEW: "ai-quality/business-review",
    WorkflowState.QA_ASSESSMENT: "ai-quality/qa-assessment",
}

DEPLOYMENT_DECISIONS = [
    "system_usage_location",
    "users",
    "usage_frequency",
    "idle_shutdown_policy",
    "restart_wait_tolerance",
    "preproduction_environment",
    "release_downtime_tolerance",
    "recovery_time_objective",
    "recovery_point_objective",
    "production_like_data_policy",
    "monthly_cost_policy",
    "operations_owner",
]

StageResultT = TypeVar("StageResultT", bound=StageResult)


class WorkflowRunner:
    """Run one task until a human gate, safe stop, or failure state."""

    def __init__(
        self,
        config: ProjectConfig,
        agents: Mapping[str, AgentDefinition],
        provider: AgentProvider,
        store: SQLiteStateStore,
        *,
        root: Path | None = None,
        github: GitHubGateway | None = None,
        git: GitWorktreeGateway | None = None,
        verification_runner: VerificationRunner | None = None,
        verification_policy: VerificationPolicy | None = None,
        stop_after_pull_request: bool = False,
    ) -> None:
        self.config = config
        self.agents = agents
        self.provider = provider
        self.store = store
        self.github = github
        self.git = git
        self.verification_runner = verification_runner
        self.verification_policy = verification_policy
        self.stop_after_pull_request = stop_after_pull_request
        self.context_builder = TaskContextBuilder(root or Path.cwd(), github=github, git=git)

    def build_graph(self) -> Any:
        """Build a graph whose verification node is deterministic and host-managed."""
        builder = StateGraph(GraphState)
        builder.add_node(self._execute_step)  # type: ignore[call-overload]
        builder.add_edge(START, "_execute_step")
        builder.add_conditional_edges(
            "_execute_step",
            self._route,
            {"continue": "_execute_step", "end": END},
        )
        return builder.compile()

    async def run(self, task_id: str) -> TaskRecord:
        """Execute from persisted state and stop at a governed boundary."""
        task = self.store.get_task(task_id)
        graph = self.build_graph()
        await graph.ainvoke(
            {"task_id": task.task_id, "state": task.state.value, "steps": 0},
            config={"recursion_limit": 100},
        )
        return self.store.get_task(task_id)

    async def run_one_stage(self, task_id: str, stage: WorkflowState) -> TaskRecord:
        """Execute one formal review after checking its exact prerequisites."""
        task = self.store.get_task(task_id)
        if task.state != stage or stage not in RESULT_MODELS:
            raise ValueError("task is not ready for the requested stage")
        reason = self._prerequisite_failure(task, stage)
        if reason is not None:
            self._publish_prerequisite_failure(task, stage, reason)
            self._move(task, WorkflowState.REWORK_REQUIRED, reason)
            return self.store.get_task(task_id)
        await self._run_agent_stage(task)
        return self.store.get_task(task_id)

    async def _execute_step(self, graph_state: GraphState) -> GraphState:
        task = self.store.get_task(graph_state["task_id"])
        before = task.state
        if before in TERMINAL_STATES:
            return {
                "task_id": task.task_id,
                "state": task.state.value,
                "steps": graph_state["steps"],
            }

        if before == WorkflowState.NEW:
            task = self._move(task, WorkflowState.REQUIREMENTS_ANALYSIS, "workflow_started")
        elif before == WorkflowState.DEPLOYMENT_CONFIGURATION:
            deployment = task.evidence.deployment_configuration
            if deployment is None or not deployment.human_approved:
                task = task.model_copy(update={"pending_human_decisions": DEPLOYMENT_DECISIONS})
                task = self._move(
                    task,
                    WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED,
                    "deployment_answers_and_approval_required",
                )
            else:
                task = self._move(
                    task.model_copy(update={"pending_human_decisions": []}),
                    WorkflowState.PLANNING,
                    "deployment_configuration_approved",
                )
        elif before == WorkflowState.REWORK_REQUIRED:
            if task.iteration >= self.config.workflow.max_iterations:
                task = self._move(task, WorkflowState.BLOCKED, "max_iterations_reached")
            else:
                task = task.model_copy(update={"iteration": task.iteration + 1})
                task = self._move(task, WorkflowState.IMPLEMENTING, "rework_started")
        elif before == WorkflowState.AUTOMATED_TESTING:
            task = self._run_verification_stage(task)
        else:
            reason = self._prerequisite_failure(task, before)
            if reason is not None:
                self._publish_prerequisite_failure(task, before, reason)
                task = self._move(task, WorkflowState.REWORK_REQUIRED, reason)
            else:
                task = await self._run_agent_stage(task)

        if task.cancel_requested and task.state not in TERMINAL_STATES:
            task = self._move(
                task.model_copy(update={"cancel_requested": False}),
                WorkflowState.FAILED,
                "cancelled_at_stage_boundary",
            )
        elif task.pause_requested and task.state not in TERMINAL_STATES:
            next_state = task.state
            task = task.model_copy(update={"resume_state": next_state, "pause_requested": False})
            task = self._move(task, WorkflowState.PAUSED, "paused_at_stage_boundary")

        return {
            "task_id": task.task_id,
            "state": task.state.value,
            "steps": graph_state["steps"] + 1,
        }

    async def _run_agent_stage(self, task: TaskRecord) -> TaskRecord:
        agent_id = STAGE_AGENT.get(task.state)
        result_model = RESULT_MODELS.get(task.state)
        if agent_id is None or agent_id not in self.agents or result_model is None:
            return self._move(task, WorkflowState.FAILED, "undefined_agent_for_stage")
        definition = self.agents[agent_id]
        try:
            context = self.context_builder.build(task, task.state, definition)
            if task.state in {
                WorkflowState.SYSTEM_REVIEW,
                WorkflowState.BUSINESS_REVIEW,
                WorkflowState.QA_ASSESSMENT,
            }:
                context["review_coverage"] = {
                    requirement_type.value: rule.model_dump(mode="json")
                    for requirement_type, rule in self.config.review_coverage.items()
                }
        except SensitiveContentError:
            return self._move(
                task,
                WorkflowState.SECURITY_INCIDENT_REQUIRES_HUMAN,
                "sensitive_agent_context_rejected",
            )
        except Exception:
            return self._move(task, WorkflowState.FAILED, "context_collection_failed")
        request = AgentRequest(
            agent_id=agent_id,
            prompt=(
                f"Execute stage {task.state.value} for Issue #{task.issue_number}. "
                "Return only a result matching the supplied stage-specific JSON Schema."
            ),
            system_prompt=definition.system_prompt,
            context=context,
            model=definition.model,
            max_turns=min(definition.max_turns, self.config.workflow.max_agent_turns),
            timeout_seconds=self.config.workflow.timeout_minutes * 60,
            max_budget_usd=self.config.budget.per_task.stop_usd,
            allowed_tools=definition.available_tools,
            forbidden_tools=definition.forbidden_tools,
            working_directory=str(self.context_builder.root),
            readable_paths=definition.readable_paths,
            writable_paths=definition.writable_paths,
            protected_paths=definition.protected_paths,
            internet_access=definition.internet_access,
            output_schema=result_model.model_json_schema(),
        )
        self.store.append_event(
            task.task_id, agent_id, "agent_stage_started", "started", {"stage": task.state}
        )
        try:
            provider_result = await self.provider.execute(request)
        except Exception:
            return self._move(task, WorkflowState.FAILED, "provider_exception_suppressed")
        new_cost = task.estimated_cost_usd + (provider_result.estimated_cost_usd or 0)
        task = task.model_copy(update={"estimated_cost_usd": new_cost})
        self.store.append_event(
            task.task_id,
            agent_id,
            "agent_stage_completed",
            provider_result.status.value,
            {
                "stage": task.state,
                "model": provider_result.model,
                "turns": provider_result.turns,
                "estimated_cost_usd": provider_result.estimated_cost_usd,
            },
        )
        if new_cost >= self.config.budget.per_task.stop_usd:
            return self._move(task, WorkflowState.BLOCKED, "per_task_budget_stop_reached")
        if provider_result.status != AgentRunStatus.SUCCESS:
            return self._move(
                task,
                WorkflowState.FAILED,
                provider_result.error_code or "provider_failure",
            )
        try:
            result = result_model.model_validate(provider_result.output)
            requirements_approval = None
            if isinstance(result, RequirementsResult):
                try:
                    formal_requirements = parse_structured_issue_requirements(
                        str(context.get("issue_body", "")),
                        source_reference=str(
                            context.get("issue_url") or f"github:issue:{task.issue_number}"
                        ),
                    )
                except ValueError:
                    result = result.model_copy(
                        update={"requirements_source": "AI_CANDIDATE", "human_approved": False}
                    )
                else:
                    if self.github is not None:
                        requirements_approval = find_requirements_approval(
                            task.issue_number,
                            formal_requirements,
                            self.github.get_issue_comments(task.issue_number),
                        )
                    result = result.model_copy(
                        update={
                            "requirements": formal_requirements,
                            "business_requirements": [
                                item.description
                                for item in formal_requirements
                                if item.type == "BUSINESS"
                            ],
                            "acceptance_criteria": [
                                criterion
                                for item in formal_requirements
                                for criterion in item.acceptance_criteria
                            ],
                            "requirements_source": "STRUCTURED_ISSUE",
                            "human_approved": requirements_approval is not None,
                        }
                    )
                    if requirements_approval is not None:
                        evidence = task.evidence.model_copy(deep=True)
                        evidence.requirements_approval = requirements_approval
                        task = task.model_copy(update={"evidence": evidence})
            self._validate_evidence_references(result)
            result = self._bind_review_target(task, result)
            self._validate_review_scope(task, result)
            task = self._persist_result(task, result)
        except (ValidationError, ValueError):
            target = (
                WorkflowState.REWORK_REQUIRED
                if task.state
                in {
                    WorkflowState.SYSTEM_REVIEW,
                    WorkflowState.BUSINESS_REVIEW,
                    WorkflowState.QA_ASSESSMENT,
                }
                else WorkflowState.FAILED
            )
            return self._move(task, target, "invalid_agent_output_rejected")

        target, passed = self._decide_target(task, result)
        if task.state in CHECK_NAMES and self.github is not None:
            try:
                self._publish_quality_gate(task, result, passed)
            except GitHubError:
                return self._move(task, WorkflowState.FAILED, "github_quality_result_failed")
        return self._move(
            task.model_copy(update={"last_summary": result.summary}),
            target,
            "stage_passed" if passed else "stage_rejected",
            details={
                "decision": result.decision,
                "run_id": result.run_id,
                "evidence_count": len(result.evidence),
                "finding_ids": [finding.id for finding in result.findings],
            },
        )

    def _run_verification_stage(self, task: TaskRecord) -> TaskRecord:
        """Verify the post-Agent worktree, then commit and publish only on exact PASS."""
        if (
            self.git is None
            or self.github is None
            or self.verification_runner is None
            or self.verification_policy is None
        ):
            return self._move(task, WorkflowState.FAILED, "verification_service_missing")
        if not task.branch or not task.evidence.developer_results:
            return self._move(task, WorkflowState.REWORK_REQUIRED, "development_result_missing")
        developer_result = task.evidence.developer_results[-1]
        changed_files = self.git.changed_files()
        if not changed_files or sorted(developer_result.changed_files) != sorted(changed_files):
            return self._move(task, WorkflowState.REWORK_REQUIRED, "changed_files_mismatch")
        try:
            verification = self.verification_runner.run(
                self.context_builder.root,
                changed_files,
                self.verification_policy,
            )
        except VerificationError:
            return self._move(task, WorkflowState.REWORK_REQUIRED, "host_verification_failed")

        evidence = task.evidence.model_copy(deep=True)
        evidence.trusted_verification_results.append(verification)
        task = self.store.save_task(task.model_copy(update={"evidence": evidence}))
        self.store.append_event(
            task.task_id,
            "verification-runner",
            "host_verification_completed",
            verification.overall_status,
            {
                "run_id": verification.run_id,
                "worktree_digest": verification.worktree_digest,
                "base_commit_sha": verification.base_commit_sha,
                "changed_files": verification.changed_files,
            },
        )
        if verification.overall_status != VerificationStatus.PASS:
            return self._move(task, WorkflowState.REWORK_REQUIRED, "host_verification_not_passed")
        if not self.verification_runner.is_current(
            self.context_builder.root, changed_files, verification
        ):
            invalid = verification.model_copy(
                update={
                    "overall_status": VerificationStatus.INVALIDATED,
                    "invalidated_reason": "worktree changed after verification",
                }
            )
            evidence = task.evidence.model_copy(deep=True)
            evidence.trusted_verification_results[-1] = invalid
            task = self.store.save_task(task.model_copy(update={"evidence": evidence}))
            return self._move(task, WorkflowState.REWORK_REQUIRED, "verification_invalidated")
        try:
            task = self._publish_development_changes(task, developer_result, verification)
        except ValueError:
            return self._move(task, WorkflowState.REWORK_REQUIRED, "traceability_evidence_invalid")
        except (GitOperationError, GitHubError):
            return self._move(task, WorkflowState.BLOCKED, "commit_push_or_pr_failed")
        if self.stop_after_pull_request:
            return self._move(
                task.model_copy(update={"resume_state": WorkflowState.SYSTEM_REVIEW}),
                WorkflowState.PAUSED,
                "pull_request_published_for_independent_review",
            )
        return self._move(task, WorkflowState.SYSTEM_REVIEW, "trusted_verification_passed")

    def _publish_development_changes(
        self,
        task: TaskRecord,
        result: DeveloperResult,
        verification: VerificationResult,
    ) -> TaskRecord:
        """Commit, bind evidence, push, then create or reuse a PR in that order."""
        if self.git is None or self.github is None:
            raise GitOperationError("Git and GitHub gateways are required before review")
        requirements = task.evidence.requirements_result
        if requirements is None:
            raise ValueError("formal requirements are required before implementation evidence")
        protected_path_approved = bool(task.context.get("protected_path_approved", False))
        traces = build_validated_traceability(
            self.context_builder.root,
            requirements,
            result,
            verification,
            protected_patterns=self.config.protected_paths,
            protected_path_approved=protected_path_approved,
            trusted_mock_files=(
                set(verification.changed_files) if isinstance(self.git, MockGitWorktree) else set()
            ),
        )
        commit_sha = self.git.commit(
            f"Issue #{task.issue_number}: AI支援による実装",
            result.changed_files,
            verification,
            protected_path_approved=protected_path_approved,
        )
        assert_references_exist_at_commit(self.context_builder.root, traces, commit_sha)
        evidence = task.evidence.model_copy(deep=True)
        evidence.trusted_verification_results[-1] = verification.model_copy(
            update={"commit_sha": commit_sha}
        )
        evidence.traceability = traces
        self._bind_findings_to_verified_commit(evidence, commit_sha)
        task = self.store.save_task(
            task.model_copy(update={"commit_sha": commit_sha, "evidence": evidence})
        )
        self.git.push_work_branch(task.branch)
        if isinstance(self.github, MockGitHubGateway):
            self.github.mark_branch_pushed(task.branch, commit_sha)
        pr_number = task.pull_request_number
        if pr_number is None:
            pr_number = self.github.create_pull_request(
                task.issue_number,
                task.branch,
                self.config.github.default_branch,
                f"Issue #{task.issue_number}: AI支援による実装",
                "人間の最終判断を前提としたレビュー用の下書きです。ai-devはマージしません。",
            )
        updated = self.store.save_task(task.model_copy(update={"pull_request_number": pr_number}))
        self.store.append_event(
            updated.task_id,
            "orchestrator",
            "changes_published",
            "success",
            {
                "branch": updated.branch,
                "commit_sha": commit_sha,
                "pr_number": pr_number,
                "verification_run_id": verification.run_id,
            },
        )
        return updated

    def _publish_quality_gate(self, task: TaskRecord, result: StageResult, passed: bool) -> None:
        if self.github is None or task.pull_request_number is None:
            raise GitHubError("Pull request is required for a formal quality gate")
        blocking = [
            finding.id
            for finding in task.evidence.unresolved_findings
            if self._finding_is_blocking(finding)
        ]
        payload = {
            "pull_request_number": task.pull_request_number,
            "commit_sha": task.commit_sha,
            "review_run_id": result.run_id,
            "decision": result.decision,
            "blocking_findings": blocking,
            "evidence_references": [reference.reference for reference in result.evidence],
            "human_summary": result.summary,
        }
        summary = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        comment_id = self.github.add_pull_request_comment(task.pull_request_number, summary)
        self.github.set_check_result(
            CHECK_NAMES[task.state],
            task.commit_sha,
            "success" if passed else "failure",
            summary,
        )
        if task.state == WorkflowState.QA_ASSESSMENT:
            self.github.set_check_result(
                "ai-quality/final",
                task.commit_sha,
                "success" if passed else "failure",
                summary,
            )
        self.store.append_event(
            task.task_id,
            "github",
            "quality_gate_published",
            "success",
            {"stage": task.state, "comment_id": comment_id, "check": CHECK_NAMES[task.state]},
        )

    def _publish_prerequisite_failure(
        self, task: TaskRecord, stage: WorkflowState, reason: str
    ) -> None:
        if self.github is None or task.pull_request_number is None or stage not in CHECK_NAMES:
            return
        payload = {
            "pull_request_number": task.pull_request_number,
            "commit_sha": task.commit_sha,
            "review_run_id": f"PRECONDITION-{stage.value}",
            "decision": Decision.INSUFFICIENT_EVIDENCE,
            "blocking_findings": [
                finding.id
                for finding in task.evidence.unresolved_findings
                if self._finding_is_blocking(finding)
            ],
            "evidence_references": [],
            "human_summary": reason,
        }
        self.github.set_check_result(
            CHECK_NAMES[stage],
            task.commit_sha,
            "failure",
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    def _persist_result(self, task: TaskRecord, result: StageResult) -> TaskRecord:
        evidence = task.evidence.model_copy(deep=True)
        if isinstance(result, RequirementsResult):
            if not result.requirements:
                raise ValueError("requirements analysis must produce identifiable requirements")
            if (
                evidence.requirements_result is not None
                and evidence.requirements_result.run_id == result.run_id
            ):
                return task
            evidence.requirements_result = result
            if not result.human_approved or (
                evidence.requirements_approval is None
                or evidence.requirements_approval.requirements_digest
                != requirements_digest(result.requirements)
            ):
                evidence.requirements_approval = None
            evidence.traceability = [
                TraceabilityRecord(requirement_id=requirement.id)
                for requirement in result.requirements
            ]
        elif isinstance(result, DeveloperResult):
            if self._append_result_once(evidence.developer_results, result):
                evidence.agent_reported_test_results.extend(result.test_results)
                self._claim_finding_fixes(evidence, result)
        elif isinstance(result, SystemReviewResult):
            if self._append_result_once(evidence.system_reviews, result):
                self._merge_findings(evidence, result, ReviewType.SYSTEM, task.commit_sha)
                self._confirm_resolutions(evidence, result, ReviewType.SYSTEM, task.commit_sha)
                self._add_review_traceability(evidence, result, ReviewType.SYSTEM)
        elif isinstance(result, BusinessReviewResult):
            if self._append_result_once(evidence.business_reviews, result):
                self._merge_findings(evidence, result, ReviewType.BUSINESS, task.commit_sha)
                self._confirm_resolutions(evidence, result, ReviewType.BUSINESS, task.commit_sha)
                self._add_review_traceability(evidence, result, ReviewType.BUSINESS)
        elif isinstance(result, QaAssessmentResult):
            if self._append_result_once(evidence.qa_assessments, result):
                self._merge_findings(evidence, result, ReviewType.QA, task.commit_sha)
                self._confirm_resolutions(evidence, result, ReviewType.QA, task.commit_sha)
                self._add_review_traceability(evidence, result, ReviewType.QA)
        return task.model_copy(update={"evidence": evidence})

    @staticmethod
    def _append_result_once(results: list[StageResultT], result: StageResultT) -> bool:
        """Treat a stage result run ID as an idempotency key."""
        if any(existing.run_id == result.run_id for existing in results):
            return False
        results.append(result)
        return True

    @staticmethod
    def _add_review_traceability(
        evidence: TaskEvidence,
        result: SystemReviewResult | BusinessReviewResult | QaAssessmentResult,
        review_type: ReviewType,
    ) -> None:
        requirements = evidence.requirements_result
        if requirements is None:
            return
        reviewed_ids = set(result.evaluated_requirement_ids)
        reference = f"review:{review_type.value}:{result.run_id}"
        updated: list[TraceabilityRecord] = []
        for record in evidence.traceability:
            if record.requirement_id not in reviewed_ids:
                updated.append(record)
                continue
            review_references = dict(record.review_references)
            review_references[review_type] = list(
                dict.fromkeys([*review_references.get(review_type, []), reference])
            )
            updated.append(record.model_copy(update={"review_references": review_references}))
        evidence.traceability = updated

    @staticmethod
    def _claim_finding_fixes(evidence: TaskEvidence, result: DeveloperResult) -> None:
        addressed = set(result.addressed_finding_ids)
        updated: list[Finding] = []
        for finding in evidence.unresolved_findings:
            if finding.id in addressed and finding.status in {
                FindingStatus.OPEN,
                FindingStatus.REOPENED,
            }:
                finding = finding.model_copy(
                    update={
                        "status": FindingStatus.FIX_CLAIMED,
                        "resolution_candidate_commit_sha": None,
                        "resolved_by_review_run_id": None,
                        "resolved_at_commit_sha": None,
                        "resolution_evidence": [],
                    }
                )
            updated.append(finding)
        evidence.unresolved_findings = updated

    @staticmethod
    def _bind_findings_to_verified_commit(evidence: TaskEvidence, commit_sha: str) -> None:
        updated: list[Finding] = []
        for finding in evidence.unresolved_findings:
            if finding.status in {
                FindingStatus.FIX_CLAIMED,
                FindingStatus.RESOLVED,
                FindingStatus.ACCEPTED_BY_HUMAN,
            }:
                finding = finding.model_copy(
                    update={
                        "status": FindingStatus.VERIFICATION_REQUIRED,
                        "resolution_candidate_commit_sha": commit_sha,
                        "resolved_by_review_run_id": None,
                        "resolved_at_commit_sha": None,
                        "resolution_evidence": [],
                    }
                )
            updated.append(finding)
        evidence.unresolved_findings = updated

    @staticmethod
    def _merge_findings(
        evidence: TaskEvidence,
        result: StageResult,
        review_type: ReviewType,
        commit_sha: str,
    ) -> None:
        current = {finding.id: finding for finding in evidence.unresolved_findings}
        for incoming in result.findings:
            existing = current.get(incoming.id)
            if existing is not None and existing.origin_review_type != review_type:
                raise ValueError("a different review type cannot overwrite an existing finding")
            if existing is None:
                current[incoming.id] = incoming.model_copy(
                    update={
                        "origin_review_type": review_type,
                        "origin_review_run_id": result.run_id,
                        "origin_commit_sha": commit_sha,
                        "status": FindingStatus.OPEN,
                    }
                )
            else:
                current[incoming.id] = incoming.model_copy(
                    update={
                        "origin_review_type": existing.origin_review_type,
                        "origin_review_run_id": existing.origin_review_run_id,
                        "origin_commit_sha": existing.origin_commit_sha,
                        "status": FindingStatus.REOPENED,
                        "recurrence": True,
                        "resolution_candidate_commit_sha": None,
                        "resolved_by_review_run_id": None,
                        "resolved_at_commit_sha": None,
                        "resolution_evidence": [],
                    }
                )
        evidence.unresolved_findings = list(current.values())

    @staticmethod
    def _confirm_resolutions(
        evidence: TaskEvidence,
        result: StageResult,
        review_type: ReviewType,
        commit_sha: str,
    ) -> None:
        if result.decision != Decision.PASS:
            return
        evidence_ids = {reference.id for reference in result.evidence}
        requested = set(result.resolved_finding_ids)
        updated: list[Finding] = []
        for finding in evidence.unresolved_findings:
            if finding.id not in requested:
                updated.append(finding)
                continue
            resolution_evidence = result.finding_resolution_evidence.get(finding.id, [])
            if (
                finding.origin_review_type != review_type
                or finding.status != FindingStatus.VERIFICATION_REQUIRED
                or finding.resolution_candidate_commit_sha != commit_sha
                or not resolution_evidence
                or not set(resolution_evidence).issubset(evidence_ids)
            ):
                raise ValueError("finding resolution lacks owner, SHA, or acceptance evidence")
            updated.append(
                finding.model_copy(
                    update={
                        "status": FindingStatus.RESOLVED,
                        "resolved_by_review_run_id": result.run_id,
                        "resolved_at_commit_sha": commit_sha,
                        "resolution_evidence": resolution_evidence,
                    }
                )
            )
        evidence.unresolved_findings = updated

    def _decide_target(self, task: TaskRecord, result: StageResult) -> tuple[WorkflowState, bool]:
        severities = {finding.severity for finding in result.findings}
        blocking = bool(
            {FindingSeverity.CRITICAL, FindingSeverity.MAJOR} & severities
            or any(finding.blocking for finding in result.findings)
        )
        minor_blocks = (
            FindingSeverity.MINOR in severities and self.config.workflow.minor_findings_block
        )
        agent_test_claim_is_not_a_gate = isinstance(result, DeveloperResult)
        mandatory_failure = (
            result.decision in {Decision.REJECT, Decision.INSUFFICIENT_EVIDENCE}
            or not result.evidence_complete
            or (not result.required_tests_passed and not agent_test_claim_is_not_a_gate)
            or not result.evidence
            or blocking
            or minor_blocks
        )
        if mandatory_failure:
            if (
                task.state == WorkflowState.QA_ASSESSMENT
                and task.iteration >= self.config.workflow.max_iterations
            ):
                return WorkflowState.BLOCKED, False
            return WorkflowState.REWORK_REQUIRED, False
        if result.decision == Decision.PASS_WITH_CONDITIONS:
            if not result.conditions:
                return WorkflowState.REWORK_REQUIRED, False
            if task.state == WorkflowState.QA_ASSESSMENT:
                return WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED, False
            return WorkflowState.REWORK_REQUIRED, False
        if isinstance(result, RequirementsResult) and not result.human_approved:
            return WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED, True
        if isinstance(result, QaAssessmentResult):
            latest_verification = (
                task.evidence.trusted_verification_results[-1]
                if task.evidence.trusted_verification_results
                else None
            )
            failure = traceability_failure(
                task.evidence.requirements_result,
                task.evidence.requirements_approval,
                task.evidence.traceability,
                latest_verification,
                commit_sha=task.commit_sha,
                config=self.config,
                require_optional=self.config.workflow.optional_requirement_traceability_required,
                issue_number=task.issue_number,
            )
            if failure is not None:
                return WorkflowState.REWORK_REQUIRED, False
        return PASS_NEXT[task.state], True

    def _validate_review_scope(self, task: TaskRecord, result: StageResult) -> None:
        if not isinstance(
            result,
            (SystemReviewResult, BusinessReviewResult, QaAssessmentResult),
        ):
            return
        requirements = task.evidence.requirements_result
        if requirements is None:
            raise ValueError("formal requirements are missing")
        review_type = (
            ReviewType.SYSTEM
            if isinstance(result, SystemReviewResult)
            else ReviewType.BUSINESS
            if isinstance(result, BusinessReviewResult)
            else ReviewType.QA
        )
        failure = review_coverage_failure(
            requirements,
            review_type,
            result.evaluated_requirement_ids,
            result.excluded_requirement_reasons,
            self.config,
        )
        if failure is not None:
            raise ValueError(failure)

    @staticmethod
    def _bind_review_target(task: TaskRecord, result: StageResult) -> StageResult:
        if not isinstance(result, (SystemReviewResult, BusinessReviewResult, QaAssessmentResult)):
            return result
        if result.reviewed_commit_sha and result.reviewed_commit_sha != task.commit_sha:
            raise ValueError("review result commit does not match the task")
        if (
            result.reviewed_pr_number is not None
            and result.reviewed_pr_number != task.pull_request_number
        ):
            raise ValueError("review result PR does not match the task")
        return result.model_copy(
            update={
                "reviewed_commit_sha": task.commit_sha,
                "reviewed_pr_number": task.pull_request_number,
            }
        )

    @staticmethod
    def _validate_evidence_references(result: StageResult) -> None:
        ids = {reference.id for reference in result.evidence}
        if len(ids) != len(result.evidence):
            raise ValueError("duplicate evidence ID")
        for finding in result.findings:
            if not set(finding.evidence).issubset(ids):
                raise ValueError("finding references unknown evidence")
        if not set(result.resolved_finding_ids).issubset(result.finding_resolution_evidence):
            raise ValueError("resolved finding must provide explicit resolution evidence")
        for references in result.finding_resolution_evidence.values():
            if not set(references).issubset(ids):
                raise ValueError("finding resolution references unknown evidence")

    def _prerequisite_failure(self, task: TaskRecord, stage: WorkflowState) -> str | None:
        if stage == WorkflowState.SYSTEM_REVIEW:
            return (
                None if self._trusted_verification_passed(task) else "trusted_verification_missing"
            )
        if stage == WorkflowState.BUSINESS_REVIEW:
            return self._business_prerequisite_failure(task)
        if stage == WorkflowState.QA_ASSESSMENT:
            return self._qa_prerequisite_failure(task)
        return None

    @staticmethod
    def _trusted_verification_passed(task: TaskRecord) -> bool:
        results = task.evidence.trusted_verification_results
        if not results:
            return False
        latest = results[-1]
        return (
            latest.overall_status == VerificationStatus.PASS
            and latest.commit_sha == task.commit_sha
            and bool(latest.results)
            and all(
                not result.required or result.status.value == "PASS" for result in latest.results
            )
        )

    def _business_prerequisite_failure(self, task: TaskRecord) -> str | None:
        reviews = task.evidence.system_reviews
        if not reviews:
            return "system_review_missing"
        latest = reviews[-1]
        if latest.decision != Decision.PASS:
            return "system_review_not_unconditional_pass"
        if latest.reviewed_pr_number != task.pull_request_number:
            return "system_review_pr_mismatch"
        if latest.reviewed_commit_sha != task.commit_sha:
            return "system_review_sha_mismatch"
        if any(not condition.resolved for condition in latest.conditions):
            return "system_review_conditions_unresolved"
        if any(
            finding.origin_review_type == ReviewType.SYSTEM
            and finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
            and self._finding_is_blocking(finding)
            for finding in task.evidence.unresolved_findings
        ):
            return "system_review_blocking_findings_unresolved"
        return None

    def _qa_prerequisite_failure(self, task: TaskRecord) -> str | None:
        if not self._trusted_verification_passed(task):
            return "trusted_verification_missing"
        for name, reviews in (
            ("system", task.evidence.system_reviews),
            ("business", task.evidence.business_reviews),
        ):
            if not reviews:
                return f"{name}_review_missing"
            review = reviews[-1]
            if review.reviewed_pr_number != task.pull_request_number:
                return f"{name}_review_pr_mismatch"
            if review.reviewed_commit_sha != task.commit_sha:
                return f"{name}_review_sha_mismatch"
            if review.decision == Decision.PASS:
                if any(not condition.resolved for condition in review.conditions):
                    return f"{name}_review_conditions_unresolved"
            elif review.decision == Decision.PASS_WITH_CONDITIONS:
                if not review.conditions or any(
                    not condition.resolved
                    or not condition.resolution_evidence
                    or condition.resolved_at_commit_sha != task.commit_sha
                    for condition in review.conditions
                ):
                    return f"{name}_review_conditions_unresolved"
                if any(
                    condition.human_approval_required for condition in review.conditions
                ) and not (
                    self.store.has_current_approval(
                        task.issue_number, f"{name}-review-conditions", task.commit_sha
                    )
                ):
                    return f"{name}_review_condition_approval_missing"
            else:
                return f"{name}_review_not_passed"
        if any(
            self._finding_is_blocking(finding)
            and not (
                finding.origin_review_type == ReviewType.QA
                and finding.status == FindingStatus.VERIFICATION_REQUIRED
                and finding.resolution_candidate_commit_sha == task.commit_sha
            )
            for finding in task.evidence.unresolved_findings
        ):
            return "blocking_findings_unresolved"
        latest_verification = (
            task.evidence.trusted_verification_results[-1]
            if task.evidence.trusted_verification_results
            else None
        )
        return traceability_failure(
            task.evidence.requirements_result,
            task.evidence.requirements_approval,
            task.evidence.traceability,
            latest_verification,
            commit_sha=task.commit_sha,
            config=self.config,
            require_optional=self.config.workflow.optional_requirement_traceability_required,
            issue_number=task.issue_number,
            review_scope={ReviewType.SYSTEM, ReviewType.BUSINESS},
        )

    @staticmethod
    def _finding_is_blocking(finding: Finding) -> bool:
        return finding.status not in {
            FindingStatus.RESOLVED,
            FindingStatus.ACCEPTED_BY_HUMAN,
        } and (
            finding.blocking
            or finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        )

    def _move(
        self,
        task: TaskRecord,
        target: WorkflowState,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> TaskRecord:
        assert_transition(task.state, target)
        previous = task.state
        updated = self.store.save_task(task.model_copy(update={"state": target}))
        self.store.append_event(
            updated.task_id,
            "orchestrator",
            "state_transition",
            reason,
            {"from": previous, "to": target, **(details or {})},
        )
        return updated

    @staticmethod
    def _route(state: GraphState) -> Literal["continue", "end"]:
        current = WorkflowState(state["state"])
        return "end" if current in TERMINAL_STATES else "continue"
