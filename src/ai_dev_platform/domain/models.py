"""Validated domain models shared by providers, workflow, and interfaces."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class WorkflowState(StrEnum):
    """Allowed task states. LLM output cannot add states dynamically."""

    NEW = "NEW"
    REQUIREMENTS_ANALYSIS = "REQUIREMENTS_ANALYSIS"
    REQUIREMENTS_APPROVAL_REQUIRED = "REQUIREMENTS_APPROVAL_REQUIRED"
    DEPLOYMENT_CONFIGURATION = "DEPLOYMENT_CONFIGURATION"
    DEPLOYMENT_CONFIGURATION_REQUIRED = "DEPLOYMENT_CONFIGURATION_REQUIRED"
    PLANNING = "PLANNING"
    DESIGNING = "DESIGNING"
    IMPLEMENTING = "IMPLEMENTING"
    AUTOMATED_TESTING = "AUTOMATED_TESTING"
    SYSTEM_REVIEW = "SYSTEM_REVIEW"
    BUSINESS_REVIEW = "BUSINESS_REVIEW"
    QA_ASSESSMENT = "QA_ASSESSMENT"
    QA_CONDITIONAL_APPROVAL_REQUIRED = "QA_CONDITIONAL_APPROVAL_REQUIRED"
    REWORK_REQUIRED = "REWORK_REQUIRED"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    PAUSED = "PAUSED"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SECURITY_INCIDENT_REQUIRES_HUMAN = "SECURITY_INCIDENT_REQUIRES_HUMAN"
    DATA_EXPOSURE_REQUIRES_HUMAN = "DATA_EXPOSURE_REQUIRES_HUMAN"


class Decision(StrEnum):
    """Structured decision accepted from an agent result."""

    PASS = "PASS"
    PASS_WITH_CONDITIONS = "PASS_WITH_CONDITIONS"
    REJECT = "REJECT"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class FindingSeverity(StrEnum):
    """Review severity with deterministic gate semantics."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    SUGGESTION = "suggestion"


class ReviewType(StrEnum):
    """Formal review type that owns findings raised by that stage."""

    SYSTEM = "SYSTEM"
    BUSINESS = "BUSINESS"
    QA = "QA"


class RequirementType(StrEnum):
    """Formal requirement categories used by review-coverage policy."""

    BUSINESS = "BUSINESS"
    FUNCTIONAL = "FUNCTIONAL"
    NON_FUNCTIONAL = "NON_FUNCTIONAL"
    SECURITY = "SECURITY"
    OPERATIONAL = "OPERATIONAL"


class ReviewCoverageRule(StrictModel):
    """Required formal reviews for one requirement category."""

    required_reviews: list[ReviewType] = Field(min_length=1)

    @field_validator("required_reviews")
    @classmethod
    def reviews_are_unique(cls, value: list[ReviewType]) -> list[ReviewType]:
        """Reject ambiguous duplicate review requirements."""
        if len(value) != len(set(value)):
            raise ValueError("required reviews must be unique")
        return value


def default_review_coverage() -> dict[RequirementType, ReviewCoverageRule]:
    """Return the governed default review coverage by requirement category."""
    return {
        RequirementType.BUSINESS: ReviewCoverageRule(
            required_reviews=[ReviewType.BUSINESS, ReviewType.QA]
        ),
        RequirementType.FUNCTIONAL: ReviewCoverageRule(
            required_reviews=[ReviewType.SYSTEM, ReviewType.BUSINESS, ReviewType.QA]
        ),
        RequirementType.NON_FUNCTIONAL: ReviewCoverageRule(
            required_reviews=[ReviewType.SYSTEM, ReviewType.QA]
        ),
        RequirementType.SECURITY: ReviewCoverageRule(
            required_reviews=[ReviewType.SYSTEM, ReviewType.QA]
        ),
        RequirementType.OPERATIONAL: ReviewCoverageRule(
            required_reviews=[ReviewType.SYSTEM, ReviewType.QA]
        ),
    }


class FindingStatus(StrEnum):
    """Auditable finding lifecycle; findings are retained instead of deleted."""

    OPEN = "OPEN"
    FIX_CLAIMED = "FIX_CLAIMED"
    VERIFICATION_REQUIRED = "VERIFICATION_REQUIRED"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"
    ACCEPTED_BY_HUMAN = "ACCEPTED_BY_HUMAN"


class TestStatus(StrEnum):
    """Normalized automated-test status."""

    PASS = "PASS"
    FAIL = "FAIL"
    NOT_RUN = "NOT_RUN"


class VerificationStatus(StrEnum):
    """Trusted host-side verification decision."""

    PASS = "PASS"
    FAIL = "FAIL"
    INVALIDATED = "INVALIDATED"


class AgentRunStatus(StrEnum):
    """Transport-level provider result."""

    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"
    REJECTED = "REJECTED"


class ProviderPreflightStageResult(StrictModel):
    """Sanitized result for one provider-boundary diagnostic request."""

    stage: Literal["models_api", "token_count_api", "messages_api", "agent_sdk"]
    status: Literal["PASS", "ERROR"]
    error_code: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def error_code_matches_status(self) -> ProviderPreflightStageResult:
        """Require one safe code only for a failed diagnostic stage."""
        if self.status == "ERROR" and self.error_code is None:
            raise ValueError("failed provider preflight stage requires an error code")
        if self.status == "PASS" and self.error_code is not None:
            raise ValueError("passed provider preflight stage cannot have an error code")
        return self


class ProviderPreflightReport(StrictModel):
    """Secret-free provider diagnostic evidence bound to one commit."""

    schema_version: Literal["1.0"] = "1.0"
    provider: Literal["mock", "claude"]
    commit_sha: str = Field(min_length=7, max_length=64, pattern=r"^[0-9a-z]+$")
    overall_status: Literal["PASS", "ERROR", "SKIPPED"]
    stages: list[ProviderPreflightStageResult] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def status_matches_stages(self) -> ProviderPreflightReport:
        """Keep skipped, passed, and failed reports internally consistent."""
        if self.overall_status == "SKIPPED":
            if self.provider != "mock" or self.stages:
                raise ValueError("only Mock preflight without stages may be skipped")
            return self
        if self.provider != "claude" or not self.stages:
            raise ValueError("Claude preflight requires at least one stage")
        expected_stages = ("models_api", "token_count_api", "messages_api", "agent_sdk")
        observed_stages = tuple(stage.stage for stage in self.stages)
        if observed_stages != expected_stages[: len(observed_stages)]:
            raise ValueError("provider preflight stages are out of order")
        failed_indexes = [
            index for index, stage in enumerate(self.stages) if stage.status == "ERROR"
        ]
        has_error = bool(failed_indexes)
        if (self.overall_status == "ERROR") != has_error:
            raise ValueError("provider preflight status does not match its stages")
        if failed_indexes and failed_indexes != [len(self.stages) - 1]:
            raise ValueError("provider preflight must stop at its first failed stage")
        if self.overall_status == "PASS" and len(self.stages) != 4:
            raise ValueError("passed provider preflight requires all four stages")
        return self


class InteractionSettings(StrictModel):
    """Human interaction frequency; mandatory gates are unaffected."""

    mode: Literal["guided", "supervised", "autonomous"] = "supervised"


class WorkflowSettings(StrictModel):
    """Workflow execution limits."""

    max_iterations: int = Field(default=3, ge=1, le=10)
    max_agent_turns: int = Field(default=40, ge=1, le=200)
    timeout_minutes: int = Field(default=60, ge=1, le=1440)
    minor_findings_block: bool = False
    optional_requirement_traceability_required: bool = False


class BudgetLimit(StrictModel):
    """Warning and hard stop in USD."""

    warning_usd: float = Field(ge=0)
    stop_usd: float = Field(gt=0)

    @field_validator("stop_usd")
    @classmethod
    def stop_must_be_positive(cls, value: float) -> float:
        """Reject nonsensical hard stops."""
        return value


class BudgetSettings(StrictModel):
    """Per-task and monthly budget limits."""

    per_task: BudgetLimit = Field(default_factory=lambda: BudgetLimit(warning_usd=5, stop_usd=10))
    monthly: BudgetLimit = Field(default_factory=lambda: BudgetLimit(warning_usd=50, stop_usd=100))
    max_execution_minutes: int = Field(default=60, ge=1)
    max_parallel_agents: int = Field(default=1, ge=1)


class TaskExecutionSettings(StrictModel):
    """MVP permits one mutating task per repository."""

    mode: Literal["single", "controlled_parallel"] = "single"
    max_parallel_tasks: int = Field(default=1, ge=1)
    allow_parallel_read_only_reviews: bool = True
    conflict_detection: bool = False
    protected_path_exclusive_lock: bool = False


class GitHubSettings(StrictModel):
    """GitHub integration settings."""

    enabled: bool = False
    gateway: Literal["mock", "gh"] = "mock"
    default_branch: str = "main"
    allowed_branch_patterns: list[str] = Field(default_factory=lambda: ["ai/*"])
    allowed_actors: list[str] = Field(default_factory=list)


class ProjectInfo(StrictModel):
    """Project identity without credentials."""

    name: str = Field(min_length=1, max_length=100)
    description: str = ""


class ProjectConfig(StrictModel):
    """Top-level project configuration."""

    schema_version: Literal["1.0"] = "1.0"
    project: ProjectInfo
    provider: Literal["mock", "claude"] = "mock"
    interaction: InteractionSettings = Field(default_factory=InteractionSettings)
    workflow: WorkflowSettings = Field(default_factory=WorkflowSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    task_execution: TaskExecutionSettings = Field(default_factory=TaskExecutionSettings)
    github: GitHubSettings = Field(default_factory=GitHubSettings)
    agents: list[str]
    protected_paths: list[str]
    quality_gates: list[str]
    review_coverage: dict[RequirementType, ReviewCoverageRule] = Field(
        default_factory=default_review_coverage
    )

    @field_validator("agents")
    @classmethod
    def agents_are_unique(cls, value: list[str]) -> list[str]:
        """Reject duplicate agent references."""
        if len(value) != len(set(value)):
            raise ValueError("agent references must be unique")
        return value

    @model_validator(mode="after")
    def every_requirement_type_has_review_coverage(self) -> ProjectConfig:
        """Require an explicit review policy for every formal requirement type."""
        missing = set(RequirementType) - set(self.review_coverage)
        if missing:
            raise ValueError("review coverage must define every requirement type")
        return self


class InternetAccess(StrictModel):
    """Network access policy for an agent."""

    mode: Literal["none", "allowlist", "unrestricted_read"] = "none"
    domains: list[str] = Field(default_factory=list)
    external_content_is_untrusted: bool = True
    allow_download: bool = False
    allow_form_submission: bool = False
    allow_authentication: bool = False
    allow_sensitive_data_transmission: bool = False
    citation_required: bool = False


class AgentDefinition(StrictModel):
    """Configurable AI role and its enforced capability envelope."""

    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    title: str
    role: str
    expertise: list[str]
    responsibilities: list[str]
    forbidden_actions: list[str]
    system_prompt: str
    provider: Literal["project_default", "mock", "claude"] = "project_default"
    model: str = "default"
    max_turns: int = Field(default=10, ge=1, le=100)
    available_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    readable_paths: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    forbidden_commands: list[str] = Field(default_factory=list)
    read_only: bool = False
    internet_access: InternetAccess = Field(default_factory=InternetAccess)
    output_schema: str
    review_criteria: list[str] = Field(default_factory=list)
    reference_materials: list[str] = Field(default_factory=list)


class AgentRequest(StrictModel):
    """Provider-independent request."""

    agent_id: str
    prompt: str
    system_prompt: str = ""
    context: dict[str, Any] = Field(default_factory=dict)
    model: str = "default"
    max_turns: int = Field(default=10, ge=1)
    timeout_seconds: float = Field(default=300, gt=0)
    max_budget_usd: float | None = Field(default=None, gt=0)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    working_directory: str = ""
    readable_paths: list[str] = Field(default_factory=list)
    writable_paths: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    internet_access: InternetAccess = Field(default_factory=InternetAccess)
    output_schema: dict[str, Any] | None = None


class AgentResult(StrictModel):
    """Provider-independent, structured agent result."""

    status: AgentRunStatus
    output: dict[str, Any] = Field(default_factory=dict)
    summary: str = ""
    model: str = ""
    turns: int = Field(default=0, ge=0)
    estimated_cost_usd: float | None = Field(default=None, ge=0)
    error_code: str | None = None


class EvidenceReference(StrictModel):
    """Safe reference to evidence stored outside normal workflow state."""

    id: str = Field(min_length=1)
    kind: Literal["file", "artifact", "github", "commit", "dataset", "metric", "mock"]
    reference: str = Field(min_length=1)
    safe_summary: str = ""


class Finding(StrictModel):
    """Actionable review finding retained until explicitly resolved."""

    id: str = Field(pattern=r"^[A-Z][A-Z0-9_-]*-\d+$")
    severity: FindingSeverity
    category: str = Field(min_length=1)
    requirement_ids: list[str] = Field(default_factory=list)
    file: str | None = None
    line: int | None = Field(default=None, ge=1)
    problem: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    business_impact: str = Field(min_length=1)
    required_fix: str = Field(min_length=1)
    acceptance_test: str = Field(min_length=1)
    blocking: bool = False
    recurrence: bool = False
    origin_review_type: ReviewType | None = None
    origin_review_run_id: str = ""
    origin_commit_sha: str = ""
    status: FindingStatus = FindingStatus.OPEN
    resolution_candidate_commit_sha: str | None = None
    resolved_by_review_run_id: str | None = None
    resolved_at_commit_sha: str | None = None
    resolution_evidence: list[str] = Field(default_factory=list)


class ReviewCondition(StrictModel):
    """A condition that prevents unconditional progression."""

    condition: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    due_or_next_stage: str = Field(min_length=1)
    human_approval_required: bool
    unmet_action: Literal["REWORK", "BLOCK", "REJECT"]
    resolved: bool = False
    resolution_evidence: list[str] = Field(default_factory=list)
    resolved_at_commit_sha: str | None = None


class StageResult(StrictModel):
    """Common fields shared by stage-specific result contracts."""

    decision: Decision
    summary: str = ""
    evidence: list[EvidenceReference] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    conditions: list[ReviewCondition] = Field(default_factory=list)
    evidence_complete: bool = True
    required_tests_passed: bool = True
    run_id: str = Field(default_factory=lambda: f"REVIEW-{uuid4()}")
    resolved_finding_ids: list[str] = Field(default_factory=list)
    finding_resolution_evidence: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator("evidence", mode="before")
    @classmethod
    def normalize_legacy_evidence(cls, value: Any) -> Any:
        """Accept old string references while normalizing them to safe references."""
        if not isinstance(value, list):
            return value
        return [
            {
                "id": f"legacy-evidence-{index}",
                "kind": "mock",
                "reference": item,
            }
            if isinstance(item, str)
            else item
            for index, item in enumerate(value, start=1)
        ]

    @field_validator("findings", mode="before")
    @classmethod
    def reject_unstructured_findings(cls, value: Any) -> Any:
        """Do not permit findings that cannot be tracked by stable ID."""
        if isinstance(value, list) and any(isinstance(item, str) for item in value):
            raise ValueError("review findings must use the structured Finding contract")
        return value


class RequirementItem(StrictModel):
    """One formally identifiable requirement and its acceptance conditions."""

    id: str = Field(min_length=1)
    type: RequirementType
    description: str = Field(min_length=1)
    acceptance_criteria: list[str] = Field(min_length=1)
    required: bool = True
    source_reference: str = Field(min_length=1)


class RequirementsResult(StageResult):
    """Confirmed requirements and scope for downstream stages."""

    requirements: list[RequirementItem] = Field(default_factory=list)
    business_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    scope: list[str] = Field(default_factory=list)
    out_of_scope: list[str] = Field(default_factory=list)
    human_decisions: list[str] = Field(default_factory=list)
    requirements_source: Literal["STRUCTURED_ISSUE", "AI_CANDIDATE"] = "AI_CANDIDATE"
    human_approved: bool = False

    @model_validator(mode="after")
    def requirement_ids_are_unique(self) -> RequirementsResult:
        """Reject ambiguous requirement identities before traceability is created."""
        ids = [requirement.id for requirement in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("requirement IDs must be unique")
        return self


class RequirementsApproval(StrictModel):
    """Human approval bound to the normalized formal-requirements digest."""

    issue_number: int = Field(ge=1)
    requirements_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    github_reference: str = Field(min_length=1)


class DeploymentConfiguration(StageResult):
    """Human-approved deployment and environment decisions."""

    deployment_target: dict[str, Any] = Field(default_factory=dict)
    environments: list[dict[str, Any]] = Field(default_factory=list)
    availability_requirements: dict[str, Any] = Field(default_factory=dict)
    recovery_requirements: dict[str, Any] = Field(default_factory=dict)
    cost_policy: dict[str, Any] = Field(default_factory=dict)
    restricted_data_policy: dict[str, Any] = Field(default_factory=dict)
    human_approved: bool = False
    approver: str | None = None


class TestRunResult(StrictModel):
    """Agent-reported test information; never trusted as a commit gate."""

    name: str = Field(min_length=1)
    status: TestStatus
    evidence_reference: str = Field(min_length=1)
    passed: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
    commit_sha: str = Field(min_length=7)


class VerificationCommand(StrictModel):
    """One host-managed command expressed only as an argv array."""

    name: str = Field(min_length=1)
    argv: list[str] = Field(min_length=1)
    required: bool = True
    timeout_seconds: int = Field(default=600, ge=1, le=3600)


class VerificationPolicy(StrictModel):
    """Host verification configuration, separate from Agent tool permissions."""

    schema_version: Literal["1.0"] = "1.0"
    commands: list[VerificationCommand] = Field(min_length=1)
    secret_scan: bool = True


class VerificationCommandResult(StrictModel):
    """Sanitized result for one deterministic host check."""

    name: str = Field(min_length=1)
    argv: list[str] = Field(min_length=1)
    required: bool = True
    status: TestStatus
    exit_code: int | None = None
    duration_seconds: float = Field(default=0, ge=0)
    evidence_reference: str = Field(min_length=1)
    summary: str = ""


class ExecutedTestCase(StrictModel):
    """One test case parsed by the host from a JUnit XML result."""

    id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    file: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "SKIP", "ERROR"]
    duration_seconds: float | None = Field(default=None, ge=0)
    evidence_reference: str = Field(min_length=1)


class VerificationResult(StrictModel):
    """Trusted result bound to one exact uncommitted worktree snapshot."""

    run_id: str = Field(default_factory=lambda: f"VERIFY-{uuid4()}")
    worktree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_commit_sha: str = Field(min_length=7)
    changed_files: list[str] = Field(min_length=1)
    commands: list[list[str]] = Field(default_factory=list)
    results: list[VerificationCommandResult] = Field(default_factory=list)
    executed_test_cases: list[ExecutedTestCase] = Field(default_factory=list)
    overall_status: VerificationStatus
    started_at: datetime
    finished_at: datetime
    commit_sha: str | None = None
    invalidated_reason: str | None = None


class RequirementImplementationReference(StrictModel):
    """Developer-proposed design and implementation files for one requirement."""

    requirement_id: str = Field(min_length=1)
    design_references: list[str] = Field(default_factory=list)
    implementation_references: list[str] = Field(default_factory=list)


class AcceptanceCriterionTestMapping(StrictModel):
    """Developer-proposed mapping from one exact criterion to executed test IDs."""

    requirement_id: str = Field(min_length=1)
    acceptance_criterion: str = Field(min_length=1)
    test_case_ids: list[str] = Field(min_length=1)


class DeveloperResult(StageResult):
    """Developer output tied to findings, files, and tests."""

    addressed_finding_ids: list[str] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    change_summary: str = ""
    added_tests: list[str] = Field(default_factory=list)
    test_results: list[TestRunResult] = Field(default_factory=list)
    unresolved_finding_ids: list[str] = Field(default_factory=list)
    unresolved_reasons: dict[str, str] = Field(default_factory=dict)
    requirement_implementations: list[RequirementImplementationReference] = Field(
        default_factory=list
    )
    acceptance_criterion_test_mappings: list[AcceptanceCriterionTestMapping] = Field(
        default_factory=list
    )


class SystemReviewResult(StageResult):
    """Technical review result for an exact PR diff and commit."""

    reviewed_commit_sha: str = ""
    reviewed_pr_number: int | None = Field(default=None, ge=1)
    reviewed_files: list[str] = Field(default_factory=list)
    evaluated_requirement_ids: list[str] = Field(default_factory=list)
    excluded_requirement_reasons: dict[str, str] = Field(default_factory=dict)


class BusinessReviewResult(StageResult):
    """Business review result with source and evaluation references."""

    evaluated_requirement_ids: list[str] = Field(default_factory=list)
    source_references: list[str] = Field(default_factory=list)
    data_as_of: str | None = None
    reviewed_commit_sha: str = ""
    reviewed_pr_number: int | None = Field(default=None, ge=1)
    excluded_requirement_reasons: dict[str, str] = Field(default_factory=dict)


class QaAssessmentResult(StageResult):
    """Integrated final quality assessment."""

    reviewed_evidence_ids: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    residual_risk_ids: list[str] = Field(default_factory=list)
    reviewed_commit_sha: str = ""
    reviewed_pr_number: int | None = Field(default=None, ge=1)
    evaluated_requirement_ids: list[str] = Field(default_factory=list)
    excluded_requirement_reasons: dict[str, str] = Field(default_factory=dict)


class TraceabilityRecord(StrictModel):
    """Requirement-to-implementation-and-test trace."""

    requirement_id: str = Field(min_length=1)
    design_references: list[str] = Field(default_factory=list)
    implementation_references: list[str] = Field(default_factory=list)
    test_references: list[str] = Field(default_factory=list)
    acceptance_criteria_test_references: dict[str, list[str]] = Field(default_factory=dict)
    review_references: dict[ReviewType, list[str]] = Field(default_factory=dict)

    @field_validator("review_references", mode="before")
    @classmethod
    def normalize_legacy_review_references(cls, value: Any) -> Any:
        """Normalize valid legacy review strings and reject arbitrary evidence text."""
        if not isinstance(value, list):
            return value
        normalized: dict[str, list[str]] = {}
        review_values = {item.value for item in ReviewType}
        for reference in value:
            if not isinstance(reference, str):
                raise ValueError("review references must be validated strings")
            parts = reference.split(":", maxsplit=2)
            if len(parts) != 3 or parts[0] != "review" or parts[1] not in review_values:
                raise ValueError("review reference format is invalid")
            normalized.setdefault(parts[1], []).append(reference)
        return normalized


class ResidualRisk(StrictModel):
    """Residual risk explicitly retained for human judgment."""

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    impact: str = Field(min_length=1)
    mitigation: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    human_accepted: bool = False


class TaskEvidence(StrictModel):
    """Quality evidence persisted as safe structure and references only."""

    requirements_result: RequirementsResult | None = None
    requirements_approval: RequirementsApproval | None = None
    deployment_configuration: DeploymentConfiguration | None = None
    developer_results: list[DeveloperResult] = Field(default_factory=list)
    agent_reported_test_results: list[TestRunResult] = Field(default_factory=list)
    trusted_verification_results: list[VerificationResult] = Field(default_factory=list)
    system_reviews: list[SystemReviewResult] = Field(default_factory=list)
    business_reviews: list[BusinessReviewResult] = Field(default_factory=list)
    qa_assessments: list[QaAssessmentResult] = Field(default_factory=list)
    unresolved_findings: list[Finding] = Field(default_factory=list)
    traceability: list[TraceabilityRecord] = Field(default_factory=list)
    residual_risks: list[ResidualRisk] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def downgrade_legacy_test_evidence(cls, value: Any) -> Any:
        """Preserve legacy test records only as untrusted Agent-reported information."""
        if not isinstance(value, dict) or "automated_test_results" not in value:
            return value
        migrated = dict(value)
        legacy = migrated.pop("automated_test_results", [])
        current = list(migrated.get("agent_reported_test_results", []))
        if isinstance(legacy, list):
            current.extend(legacy)
        migrated["agent_reported_test_results"] = current
        return migrated


class QualityArtifactEnvelope(StrictModel):
    """Sanitized review result transferred between quality-gate stages."""

    artifact_version: Literal["1.0"] = "1.0"
    stage: ReviewType
    issue_number: int = Field(ge=1)
    pull_request_number: int = Field(ge=1)
    commit_sha: str = Field(min_length=7)
    review_run_id: str = Field(min_length=1)
    decision: Decision
    blocking_finding_ids: list[str] = Field(default_factory=list)
    evidence_references: list[str] = Field(default_factory=list)
    human_summary: str
    result: dict[str, Any]


class TaskRecord(StrictModel):
    """Persisted task state stripped of sensitive content."""

    task_id: str
    issue_number: int = Field(ge=1)
    state: WorkflowState = WorkflowState.NEW
    resume_state: WorkflowState | None = None
    iteration: int = Field(default=0, ge=0)
    commit_sha: str
    branch: str = ""
    pull_request_number: int | None = Field(default=None, ge=1)
    pause_requested: bool = False
    cancel_requested: bool = False
    version: int = Field(default=0, ge=0)
    context: dict[str, Any] = Field(default_factory=dict)
    evidence: TaskEvidence = Field(default_factory=TaskEvidence)
    pending_human_decisions: list[str] = Field(default_factory=list)
    last_summary: str = ""
    estimated_cost_usd: float = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ApprovalRecord(StrictModel):
    """Explicit approval bound to issue, stage, and commit."""

    issue_number: int = Field(ge=1)
    pull_request_number: int | None = Field(default=None, ge=1)
    stage: str = Field(min_length=1)
    commit_sha: str = Field(min_length=7)
    approver: str = Field(min_length=1)
    approved: bool
    reason: str = ""
    conditions: list[str] = Field(default_factory=list)
    github_record_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class IssueDraft(StrictModel):
    """Business-oriented GitHub Issue candidate."""

    title: str = Field(min_length=1, max_length=120)
    purpose: str
    background: str = ""
    current_problem: str = ""
    scope: list[str]
    out_of_scope: list[str] = Field(default_factory=list)
    business_requirements: list[str]
    functional_requirements: list[str] = Field(default_factory=list)
    non_functional_requirements: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str]
    constraints: list[str] = Field(default_factory=list)
    user_impact: str = ""
    business_impact: str = ""
    data_impact: str = ""
    security_impact: str = ""
    production_impact: str = ""
    quality_risks: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)
    human_decisions_required: list[str] = Field(default_factory=list)


class IssueData(StrictModel):
    """GitHub Issue data used as untrusted workflow input."""

    number: int = Field(ge=1)
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    url: str = ""


class IssueComment(StrictModel):
    """GitHub Issue comment metadata used to verify a human requirements approval."""

    body: str
    author: str = Field(min_length=1)
    created_at: datetime
    url: str = Field(min_length=1)
    author_is_bot: bool = False


class SourcePackageFile(StrictModel):
    """One packaged source entry and its content digest."""

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SourcePackageManifest(StrictModel):
    """Clean-commit provenance embedded in a formal source package."""

    schema_version: Literal["1.0"] = "1.0"
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    git_status_clean: bool
    generated_at: datetime
    files: list[SourcePackageFile]
    package_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class PullRequestData(StrictModel):
    """Pull request metadata bound to the reviewed head commit."""

    number: int = Field(ge=1)
    title: str
    body: str = ""
    head_branch: str
    base_branch: str
    head_sha: str = Field(min_length=7)
    url: str = ""


class ChangedFile(StrictModel):
    """Safe pull-request file summary."""

    path: str = Field(min_length=1)
    status: Literal["added", "modified", "removed", "renamed", "copied", "changed"]
    additions: int = Field(default=0, ge=0)
    deletions: int = Field(default=0, ge=0)


class DeploymentQuestion(StrictModel):
    """Business-facing question used to confirm an environment decision."""

    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    benefits: list[str] = Field(min_length=1)
    drawbacks: list[str] = Field(min_length=1)
    recommendation: str = Field(min_length=1)
    recommendation_reason: str = Field(min_length=1)


class ConversationAnswer(StrictModel):
    """One sanitized answer in a project or Issue conversation."""

    question_id: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    answered_by: str = Field(min_length=1)
    answered_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConversationSession(StrictModel):
    """Persistent session containing decisions, not credentials or production data."""

    session_id: str = Field(min_length=1)
    project_name: str = Field(min_length=1)
    issue_number: int | None = Field(default=None, ge=1)
    questions: list[DeploymentQuestion] = Field(default_factory=list)
    answers: list[ConversationAnswer] = Field(default_factory=list)
    confirmed_issue_fields: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
