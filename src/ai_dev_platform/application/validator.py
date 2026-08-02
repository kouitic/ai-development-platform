"""Project validation across configuration, workflow, security, and data policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ai_dev_platform.config.loader import ConfigError, load_config, read_yaml, validate_with_schema
from ai_dev_platform.domain.models import WorkflowState
from ai_dev_platform.security.paths import production_like_files
from ai_dev_platform.security.runtime import CommandPolicy, PolicyViolation
from ai_dev_platform.security.scanner import scan_tree

Severity = Literal["error", "warning"]

REQUIRED_FILES = (
    ".ai-dev/project.yaml",
    ".ai-dev/workflows/default.yaml",
    ".ai-dev/policies/security.yaml",
    ".ai-dev/policies/data-governance.yaml",
    ".ai-dev/policies/approvals.yaml",
    ".ai-dev/policies/internet-access.yaml",
    ".ai-dev/policies/verification.yaml",
    ".ai-dev/schemas/project.schema.json",
    ".ai-dev/schemas/agent.schema.json",
    ".ai-dev/schemas/workflow.schema.json",
    ".ai-dev/schemas/stage-result.schema.json",
    ".ai-dev/schemas/requirements-result.schema.json",
    ".ai-dev/schemas/deployment-configuration.schema.json",
    ".ai-dev/schemas/developer-result.schema.json",
    ".ai-dev/schemas/system-review-result.schema.json",
    ".ai-dev/schemas/business-review-result.schema.json",
    ".ai-dev/schemas/qa-assessment-result.schema.json",
    ".ai-dev/schemas/verification.schema.json",
    ".github/ISSUE_TEMPLATE/ai-development.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".github/workflows/ai-quality-gates.yml",
    "AGENTS.md",
    "CODEOWNERS",
    ".env.example",
    ".gitignore",
    "README.md",
)

DEFINED_TOOLS = {
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "WebRead",
}

HOST_ONLY_TOOLS = {"Test", "Git", "GitHubIssue", "GitHubComment", "PullRequest", "Check"}

RESULT_SCHEMAS = (
    "stage-result.schema.json",
    "requirements-result.schema.json",
    "deployment-configuration.schema.json",
    "developer-result.schema.json",
    "system-review-result.schema.json",
    "business-review-result.schema.json",
    "qa-assessment-result.schema.json",
)

ROLE_RESULT_SCHEMA = {
    "conversation": "requirements-result.schema.json",
    "developer": "developer-result.schema.json",
    "system-reviewer": "system-review-result.schema.json",
    "business-reviewer": "business-review-result.schema.json",
    "qa": "qa-assessment-result.schema.json",
}


def _static_prefix(pattern: str) -> str:
    """Return the non-glob path prefix used for conservative overlap checks."""
    wildcard_positions = [
        position for marker in ("*", "?", "[") if (position := pattern.find(marker)) >= 0
    ]
    end = min(wildcard_positions) if wildcard_positions else len(pattern)
    return pattern[:end].rstrip("/")


def _patterns_overlap(left: str, right: str) -> bool:
    left_prefix = _static_prefix(left)
    right_prefix = _static_prefix(right)
    if not left_prefix or not right_prefix:
        return True
    return (
        left_prefix == right_prefix
        or left_prefix.startswith(f"{right_prefix}/")
        or right_prefix.startswith(f"{left_prefix}/")
    )


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """A sanitized validation error or warning."""

    severity: Severity
    code: str
    message: str
    path: str = ""


def _schema_checks(root: Path) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    config_root = root / ".ai-dev"
    pairs = [
        (config_root / "project.yaml", config_root / "schemas" / "project.schema.json"),
        (
            config_root / "workflows" / "default.yaml",
            config_root / "schemas" / "workflow.schema.json",
        ),
        (
            config_root / "policies" / "verification.yaml",
            config_root / "schemas" / "verification.schema.json",
        ),
    ]
    agents_dir = config_root / "agents"
    if agents_dir.exists():
        pairs.extend(
            (path, config_root / "schemas" / "agent.schema.json")
            for path in sorted(agents_dir.glob("*.yaml"))
        )
    for yaml_path, schema_path in pairs:
        if not yaml_path.exists() or not schema_path.exists():
            continue
        try:
            value = read_yaml(yaml_path)
            for error in validate_with_schema(value, schema_path):
                issues.append(
                    ValidationIssue("error", "json_schema", error, str(yaml_path.relative_to(root)))
                )
        except ConfigError as exc:
            issues.append(
                ValidationIssue(
                    "error", "invalid_config", str(exc), str(yaml_path.relative_to(root))
                )
            )
    for name in RESULT_SCHEMAS:
        schema_path = config_root / "schemas" / name
        if not schema_path.exists():
            continue
        try:
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError):
            issues.append(
                ValidationIssue(
                    "error", "invalid_result_schema", "invalid result JSON Schema", name
                )
            )
    return issues


def _workflow_checks(workflow: dict[str, object]) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    raw_nodes = workflow.get("nodes", [])
    nodes = set(raw_nodes) if isinstance(raw_nodes, list) else set()
    allowed = {state.value for state in WorkflowState}
    deterministic = workflow.get("deterministic_nodes", [])
    if not isinstance(deterministic, list) or "AUTOMATED_TESTING" not in deterministic:
        issues.append(
            ValidationIssue(
                "error",
                "automated_testing_not_deterministic",
                "AUTOMATED_TESTING must be a host-managed deterministic node",
            )
        )
    for node in sorted(nodes - allowed):
        issues.append(ValidationIssue("error", "undefined_workflow_node", str(node)))
    transitions = workflow.get("transitions", {})
    if not isinstance(transitions, dict):
        return [ValidationIssue("error", "invalid_transitions", "transitions must be a mapping")]
    adjacency: dict[str, set[str]] = {str(node): set() for node in nodes}
    for source, targets in transitions.items():
        if source not in nodes:
            issues.append(ValidationIssue("error", "undefined_transition_source", str(source)))
        if not isinstance(targets, list):
            issues.append(ValidationIssue("error", "invalid_transition_targets", str(source)))
            continue
        for target in targets:
            if target not in nodes:
                issues.append(ValidationIssue("error", "undefined_transition_target", str(target)))
            adjacency.setdefault(str(source), set()).add(str(target))
    start = str(workflow.get("start", "NEW"))
    reachable: set[str] = set()
    pending = [start]
    while pending:
        node = pending.pop()
        if node in reachable:
            continue
        reachable.add(node)
        pending.extend(adjacency.get(node, set()) - reachable)
    externally_reachable = {
        "PAUSED",
        "SECURITY_INCIDENT_REQUIRES_HUMAN",
        "DATA_EXPOSURE_REQUIRES_HUMAN",
    }
    for node in sorted(nodes - reachable - externally_reachable):
        issues.append(ValidationIssue("error", "unreachable_workflow_node", str(node)))
    if "HUMAN_APPROVAL_REQUIRED" not in reachable:
        issues.append(
            ValidationIssue("error", "missing_human_gate", "human approval gate is unreachable")
        )
    if "COMPLETED" in adjacency and any(
        source != "HUMAN_APPROVAL_REQUIRED" and "COMPLETED" in targets
        for source, targets in adjacency.items()
    ):
        issues.append(
            ValidationIssue("error", "approval_bypass", "COMPLETED has a path bypassing approval")
        )
    rework = workflow.get("rework", {})
    if not isinstance(rework, dict) or not isinstance(rework.get("max_iterations"), int):
        issues.append(
            ValidationIssue("error", "unbounded_rework", "rework cycle requires max_iterations")
        )
    return issues


def validate_project(root: Path) -> list[ValidationIssue]:
    """Validate a generated project and return all safe-to-display findings."""
    root = root.resolve()
    issues = [
        ValidationIssue("error", "missing_file", "required file is missing", relative)
        for relative in REQUIRED_FILES
        if not (root / relative).exists()
    ]
    issues.extend(_schema_checks(root))
    try:
        loaded = load_config(root)
    except ConfigError as exc:
        issues.append(ValidationIssue("error", "config_model", str(exc)))
        loaded = None

    if loaded is not None:
        protected = set(loaded.project.protected_paths)
        for agent in loaded.agents.values():
            undefined = (set(agent.available_tools) | set(agent.forbidden_tools)) - DEFINED_TOOLS
            for tool in sorted(undefined):
                issues.append(ValidationIssue("error", "undefined_tool", f"{agent.id}: {tool}"))
            for tool in sorted(set(agent.available_tools) & HOST_ONLY_TOOLS):
                issues.append(ValidationIssue("error", "host_tool_declared", f"{agent.id}: {tool}"))
            if agent.read_only and agent.writable_paths:
                issues.append(
                    ValidationIssue("error", "read_only_write", f"{agent.id} has writable paths")
                )
            schema_path = root / agent.output_schema
            if not schema_path.exists():
                issues.append(
                    ValidationIssue(
                        "error",
                        "missing_agent_output_schema",
                        f"{agent.id} output schema does not exist",
                        agent.output_schema,
                    )
                )
            expected_schema = ROLE_RESULT_SCHEMA.get(agent.role)
            if expected_schema is not None and schema_path.name != expected_schema:
                issues.append(
                    ValidationIssue(
                        "error",
                        "wrong_stage_result_schema",
                        f"{agent.id} must use {expected_schema}",
                        agent.output_schema,
                    )
                )
            agent_protected = set(agent.protected_paths)
            for write_pattern in agent.writable_paths:
                for protected_pattern in protected:
                    covered_by_agent_guard = any(
                        _patterns_overlap(protected_pattern, guard) for guard in agent_protected
                    )
                    if (
                        _patterns_overlap(write_pattern, protected_pattern)
                        and not covered_by_agent_guard
                    ):
                        issues.append(
                            ValidationIssue(
                                "error",
                                "protected_write_conflict",
                                f"{agent.id} write scope conflicts with a protected path",
                                protected_pattern,
                            )
                        )
        issues.extend(_workflow_checks(loaded.workflow))
        if loaded.verification is not None:
            names = [command.name for command in loaded.verification.commands]
            if len(names) != len(set(names)):
                issues.append(
                    ValidationIssue(
                        "error", "duplicate_verification_command", "command names must be unique"
                    )
                )
            command_policy = CommandPolicy(
                allowed_prefixes=tuple(
                    tuple(command.argv) for command in loaded.verification.commands
                )
            )
            for command in loaded.verification.commands:
                try:
                    command_policy.validate(command.argv)
                except PolicyViolation:
                    issues.append(
                        ValidationIssue(
                            "error",
                            "unsafe_verification_command",
                            f"verification command is unsafe: {command.name}",
                        )
                    )
        required_gates = {"system-review", "business-review", "qa-assessment", "human-approval"}
        missing_gates = required_gates - set(loaded.project.quality_gates)
        for gate in sorted(missing_gates):
            issues.append(ValidationIssue("error", "missing_quality_gate", gate))

    for legacy_workflow in (
        ".github/workflows/system-review.yml",
        ".github/workflows/business-review.yml",
        ".github/workflows/qa-assessment.yml",
    ):
        if (root / legacy_workflow).exists():
            issues.append(
                ValidationIssue(
                    "error",
                    "duplicate_quality_workflow",
                    "formal AI quality gates must use ai-quality-gates.yml only",
                    legacy_workflow,
                )
            )

    for finding in scan_tree(root):
        issues.append(
            ValidationIssue(
                "error",
                "secret_detected",
                f"secret-like value detected at line {finding.line}; value suppressed",
                str(finding.path),
            )
        )
    for path in production_like_files(root):
        issues.append(
            ValidationIssue(
                "error",
                "production_like_data",
                "production-like data is not allowed in the normal project tree",
                str(path),
            )
        )
    return issues
