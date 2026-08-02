"""Provider selection isolated from CLI commands."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Literal

from ai_dev_platform.domain.models import ProjectConfig
from ai_dev_platform.providers.base import AgentProvider
from ai_dev_platform.providers.claude import ClaudeAgentProvider
from ai_dev_platform.providers.mock import MockAgentProvider
from ai_dev_platform.security.github_context import (
    load_trusted_development_context,
    load_trusted_github_context,
)

ProviderPurpose = Literal["quality", "development"]


def create_provider(
    config: ProjectConfig,
    *,
    root: Path | None = None,
    purpose: ProviderPurpose = "quality",
    issue_number: int | None = None,
) -> AgentProvider:
    """Create Mock freely, but authorize Claude only from a validated GitHub event."""
    selected = os.getenv("AI_DEV_PROVIDER", config.provider).lower()
    if selected == "mock":
        return MockAgentProvider()
    if selected == "claude":
        _assert_claude_runtime_allowed(
            config,
            root or Path.cwd(),
            purpose=purpose,
            issue_number=issue_number,
        )
        return ClaudeAgentProvider()
    raise ValueError("unsupported provider; allowed values are mock and claude")


def _assert_claude_runtime_allowed(
    config: ProjectConfig,
    root: Path,
    *,
    purpose: ProviderPurpose,
    issue_number: int | None,
) -> None:
    """Reject environment-only attestations and require a trusted GitHub payload."""
    try:
        sdk_spec = importlib.util.find_spec("claude_agent_sdk")
    except (ImportError, ValueError):
        sdk_spec = None
    if sdk_spec is None:
        raise ValueError(
            "Claude Agent SDK is not installed; install the project with the 'claude' extra"
        )
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise ValueError("Claude execution requires a configured Anthropic credential")
    if config.github.gateway != "gh" or not config.github.enabled:
        raise ValueError("Claude execution requires the real GitHub gateway")
    if purpose == "development":
        if issue_number is None:
            raise ValueError("Claude development execution requires an Issue number")
        load_trusted_development_context(root, config.github, issue_number=issue_number)
    else:
        load_trusted_github_context(root, config.github)
    if config.budget.per_task.stop_usd <= 0 or config.budget.max_execution_minutes <= 0:
        raise ValueError("Claude execution requires a positive execution budget")
