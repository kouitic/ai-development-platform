"""Provider abstraction for LLM-backed agents."""

from typing import Protocol, runtime_checkable

from ai_dev_platform.domain.models import AgentRequest, AgentResult


@runtime_checkable
class AgentProvider(Protocol):
    """Execute an agent request without exposing provider details to the domain."""

    async def execute(self, request: AgentRequest) -> AgentResult:
        """Execute one bounded request and return a structured result."""
        ...
