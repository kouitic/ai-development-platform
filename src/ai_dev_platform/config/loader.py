"""YAML configuration loader with Pydantic and JSON Schema validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from ai_dev_platform.domain.models import AgentDefinition, ProjectConfig, VerificationPolicy


class ConfigError(ValueError):
    """A sanitized configuration error."""


@dataclass(slots=True)
class LoadedConfig:
    """Project configuration and its referenced agent definitions."""

    project: ProjectConfig
    agents: dict[str, AgentDefinition] = field(default_factory=dict)
    workflow: dict[str, Any] = field(default_factory=dict)
    verification: VerificationPolicy | None = None


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a UTF-8 YAML mapping."""
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ConfigError(f"invalid YAML file: {path.name}") from exc
    if not isinstance(value, dict):
        raise ConfigError(f"YAML root must be a mapping: {path.name}")
    return value


def validate_with_schema(value: dict[str, Any], schema_path: Path) -> list[str]:
    """Return JSON Schema errors without exposing secret values."""
    import json

    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid JSON Schema: {schema_path.name}") from exc
    validator = Draft202012Validator(schema)
    return [
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
    ]


def load_config(root: Path) -> LoadedConfig:
    """Load project, agents, and workflow from `.ai-dev`."""
    config_root = root / ".ai-dev"
    project_raw = read_yaml(config_root / "project.yaml")
    try:
        project = ProjectConfig.model_validate(project_raw)
    except ValidationError as exc:
        raise ConfigError("project.yaml failed model validation") from exc

    agents: dict[str, AgentDefinition] = {}
    roles: set[str] = set()
    for agent_id in project.agents:
        raw = read_yaml(config_root / "agents" / f"{agent_id}.yaml")
        try:
            agent = AgentDefinition.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"agent definition failed validation: {agent_id}") from exc
        if agent.id != agent_id:
            raise ConfigError(f"agent id does not match file reference: {agent_id}")
        if agent.role in roles:
            raise ConfigError(f"duplicate agent role: {agent.role}")
        roles.add(agent.role)
        agents[agent.id] = agent

    workflow = read_yaml(config_root / "workflows" / "default.yaml")
    try:
        verification = VerificationPolicy.model_validate(
            read_yaml(config_root / "policies" / "verification.yaml")
        )
    except ValidationError as exc:
        raise ConfigError("verification policy failed model validation") from exc
    return LoadedConfig(
        project=project,
        agents=agents,
        workflow=workflow,
        verification=verification,
    )
