from pathlib import Path

import yaml

from ai_dev_platform.application.init_service import InitConflictError, initialize_project
from ai_dev_platform.application.validator import REQUIRED_FILES, validate_project
from ai_dev_platform.config.loader import ConfigError, load_config, read_yaml


def error_codes(root: Path) -> set[str]:
    return {issue.code for issue in validate_project(root) if issue.severity == "error"}


def test_init_generates_required_project_and_substitutes_name(tmp_path: Path) -> None:
    root = tmp_path / "new"
    root.mkdir()
    result = initialize_project(root, "sample-system")
    assert len(result.created) >= 35
    assert all((root / path).exists() for path in result.created)
    assert all((root / path).exists() for path in REQUIRED_FILES)
    assert read_yaml(root / ".ai-dev" / "project.yaml")["project"]["name"] == "sample-system"
    assert ".env.*" in (root / ".gitignore").read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY=\n" in (root / ".env.example").read_text(encoding="utf-8")
    assert error_codes(root) == set()


def test_init_stops_before_writing_when_any_file_conflicts(tmp_path: Path) -> None:
    root = tmp_path / "existing"
    root.mkdir()
    marker = root / "README.md"
    marker.write_text("keep me", encoding="utf-8")
    try:
        initialize_project(root, "sample")
    except InitConflictError as exc:
        assert Path("README.md") in exc.conflicts
    else:
        raise AssertionError("conflict was not reported")
    assert marker.read_text(encoding="utf-8") == "keep me"
    assert not (root / ".ai-dev" / "project.yaml").exists()


def test_invalid_yaml_is_reported(initialized_project: Path) -> None:
    path = initialized_project / ".ai-dev" / "project.yaml"
    path.write_text("project: [", encoding="utf-8")
    codes = error_codes(initialized_project)
    assert "invalid_config" in codes or "config_model" in codes


def test_json_schema_violation_is_reported(initialized_project: Path) -> None:
    path = initialized_project / ".ai-dev" / "project.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["schema_version"] = "99"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert "json_schema" in error_codes(initialized_project)


def test_undefined_agent_reference_is_reported(initialized_project: Path) -> None:
    path = initialized_project / ".ai-dev" / "project.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["agents"].append("missing-agent")
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    try:
        load_config(initialized_project)
    except ConfigError as exc:
        assert "missing-agent" in str(exc) or "invalid YAML" in str(exc)
    else:
        raise AssertionError("undefined agent was accepted")


def test_undefined_tool_and_read_only_write_are_reported(initialized_project: Path) -> None:
    path = initialized_project / ".ai-dev" / "agents" / "qa.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["available_tools"].append("UnknownPowerTool")
    raw["writable_paths"] = ["src/**"]
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    codes = error_codes(initialized_project)
    assert {"undefined_tool", "read_only_write"} <= codes


def test_host_only_tool_and_unsafe_verification_command_are_rejected(
    initialized_project: Path,
) -> None:
    agent_path = initialized_project / ".ai-dev" / "agents" / "developer.yaml"
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    agent["available_tools"].append("Test")
    agent_path.write_text(yaml.safe_dump(agent, allow_unicode=True), encoding="utf-8")

    policy_path = initialized_project / ".ai-dev" / "policies" / "verification.yaml"
    policy = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    policy["commands"][0]["argv"] = ["python", "-c", "print('ok'); printenv"]
    policy_path.write_text(yaml.safe_dump(policy, allow_unicode=True), encoding="utf-8")
    codes = error_codes(initialized_project)
    assert "host_tool_declared" in codes
    assert "unsafe_verification_command" in codes


def test_partial_protected_path_overlap_is_reported(initialized_project: Path) -> None:
    path = initialized_project / ".ai-dev" / "agents" / "developer.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    raw["writable_paths"].append(".github/workflows/**")
    raw["protected_paths"].remove(".github/**")
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    assert "protected_write_conflict" in error_codes(initialized_project)


def test_missing_gate_and_undefined_workflow_node_are_reported(initialized_project: Path) -> None:
    project_path = initialized_project / ".ai-dev" / "project.yaml"
    project = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    project["quality_gates"].remove("qa-assessment")
    project_path.write_text(yaml.safe_dump(project), encoding="utf-8")

    workflow_path = initialized_project / ".ai-dev" / "workflows" / "default.yaml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    workflow["nodes"].append("MADE_UP")
    workflow_path.write_text(yaml.safe_dump(workflow), encoding="utf-8")
    codes = error_codes(initialized_project)
    assert "missing_quality_gate" in codes
    assert "undefined_workflow_node" in codes


def test_production_like_file_is_rejected(initialized_project: Path) -> None:
    path = initialized_project / "production-like-data" / "customer.csv"
    path.parent.mkdir()
    path.write_text("synthetic-looking but restricted", encoding="utf-8")
    assert "production_like_data" in error_codes(initialized_project)
