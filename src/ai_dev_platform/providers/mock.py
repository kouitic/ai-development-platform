"""Deterministic provider used by tests and offline development."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterable
from copy import deepcopy

from ai_dev_platform.domain.models import (
    AgentRequest,
    AgentResult,
    AgentRunStatus,
    Decision,
)


class MockAgentProvider:
    """Return scripted results, then safe PASS results by default."""

    def __init__(
        self,
        scripted_results: Iterable[AgentResult | Exception] | None = None,
        *,
        delay_seconds: float = 0,
    ) -> None:
        self._results: deque[AgentResult | Exception] = deque(scripted_results or [])
        self.delay_seconds = delay_seconds
        self.requests: list[AgentRequest] = []

    async def execute(self, request: AgentRequest) -> AgentResult:
        """Record the request and return the next deterministic result."""
        self.requests.append(deepcopy(request))
        if self.delay_seconds:
            try:
                async with asyncio.timeout(request.timeout_seconds):
                    await asyncio.sleep(self.delay_seconds)
            except TimeoutError:
                return AgentResult(
                    status=AgentRunStatus.TIMEOUT,
                    error_code="provider_timeout",
                    summary="Mock provider timed out.",
                )
        if self._results:
            result = self._results.popleft()
            if isinstance(result, Exception):
                raise result
            return deepcopy(result)
        output: dict[str, object] = {
            "decision": Decision.PASS.value,
            "summary": f"{request.agent_id} completed the requested stage.",
            "evidence": [
                {
                    "id": "mock-evidence-1",
                    "kind": "mock",
                    "reference": "mock-evidence",
                    "safe_summary": "Deterministic offline evidence.",
                }
            ],
            "findings": [],
            "conditions": [],
            "evidence_complete": True,
            "required_tests_passed": True,
            "resolved_finding_ids": [],
            "finding_resolution_evidence": {},
        }
        schema_title = str((request.output_schema or {}).get("title", ""))
        if schema_title == "RequirementsResult":
            output.update(
                {
                    "requirements": [
                        {
                            "id": "BR-001",
                            "type": "BUSINESS",
                            "description": "Satisfy the Issue request",
                            "acceptance_criteria": ["Required tests and reviews pass"],
                            "required": True,
                            "source_reference": "ai-candidate:issue-analysis",
                        }
                    ],
                    "business_requirements": ["BR-001: satisfy the Issue request"],
                    "acceptance_criteria": ["AC-001: required tests and reviews pass"],
                    "scope": ["Issue-defined change"],
                    "out_of_scope": ["production deployment"],
                    "human_decisions": [],
                    "requirements_source": "AI_CANDIDATE",
                    "human_approved": False,
                }
            )
        elif schema_title == "DeveloperResult":
            previous = request.context.get("previous_findings", [])
            addressed = [
                str(item.get("id"))
                for item in previous
                if isinstance(item, dict) and item.get("id")
            ]
            test_results: list[dict[str, object]] = [
                {
                    "name": "agent-self-reported-tests",
                    "status": "PASS",
                    "evidence_reference": "mock-evidence-1",
                    "passed": 1,
                    "failed": 0,
                    "skipped": 0,
                    "commit_sha": str(request.context.get("commit_sha", "mock000")),
                }
            ]
            requirements = [
                item
                for item in request.context.get("requirements", [])
                if isinstance(item, dict) and item.get("id")
            ]
            raw_changed_files = request.context.get("changed_files", [])
            changed_files = [
                str(item.get("path", "")) if isinstance(item, dict) else str(item)
                for item in raw_changed_files
            ]
            changed_files = [item for item in changed_files if item]
            verified_test_cases = request.context.get("verified_test_cases", [])
            test_case_ids = [
                str(item.get("id"))
                for item in verified_test_cases
                if isinstance(item, dict) and item.get("id")
            ] or ["tests/test_mock.py::test_required_behavior"]
            output.update(
                {
                    "addressed_finding_ids": addressed,
                    "changed_files": changed_files,
                    "change_summary": "Deterministic mock implementation result.",
                    "added_tests": ["mock-required-tests"],
                    "test_results": test_results,
                    "unresolved_finding_ids": [],
                    "unresolved_reasons": {},
                    "requirement_implementations": [
                        {
                            "requirement_id": str(requirement["id"]),
                            "design_references": ["docs/design/traceability.md#要件対応"],
                            "implementation_references": changed_files[:1],
                        }
                        for requirement in requirements
                    ],
                    "acceptance_criterion_test_mappings": [
                        {
                            "requirement_id": str(requirement["id"]),
                            "acceptance_criterion": str(criterion),
                            "test_case_ids": test_case_ids[:1],
                        }
                        for requirement in requirements
                        for criterion in requirement.get("acceptance_criteria", [])
                    ],
                }
            )
        elif schema_title == "SystemReviewResult":
            resolved = self._resolvable_findings(request, "SYSTEM")
            requirement_ids = [
                str(item.get("id"))
                for item in request.context.get("requirements", [])
                if isinstance(item, dict) and item.get("id")
            ]
            output.update(
                {
                    "reviewed_commit_sha": str(request.context.get("commit_sha", "")),
                    "reviewed_pr_number": request.context.get("pull_request_number"),
                    "reviewed_files": [
                        str(item.get("path", ""))
                        for item in request.context.get("changed_files", [])
                        if isinstance(item, dict)
                    ],
                    "evaluated_requirement_ids": requirement_ids,
                    "excluded_requirement_reasons": {},
                    "resolved_finding_ids": resolved,
                    "finding_resolution_evidence": {
                        finding_id: ["mock-evidence-1"] for finding_id in resolved
                    },
                }
            )
        elif schema_title == "BusinessReviewResult":
            resolved = self._resolvable_findings(request, "BUSINESS")
            requirement_ids = [
                str(item.get("id"))
                for item in request.context.get("requirements", [])
                if isinstance(item, dict) and item.get("id")
            ]
            output.update(
                {
                    "evaluated_requirement_ids": requirement_ids,
                    "source_references": ["mock-evidence-1"],
                    "data_as_of": None,
                    "reviewed_commit_sha": str(request.context.get("commit_sha", "")),
                    "reviewed_pr_number": request.context.get("pull_request_number"),
                    "excluded_requirement_reasons": {},
                    "resolved_finding_ids": resolved,
                    "finding_resolution_evidence": {
                        finding_id: ["mock-evidence-1"] for finding_id in resolved
                    },
                }
            )
        elif schema_title == "QaAssessmentResult":
            resolved = self._resolvable_findings(request, "QA")
            requirement_ids = [
                str(item.get("id"))
                for item in request.context.get("requirements", [])
                if isinstance(item, dict) and item.get("id")
            ]
            output.update(
                {
                    "reviewed_evidence_ids": ["mock-evidence-1"],
                    "contradictions": [],
                    "residual_risk_ids": [],
                    "reviewed_commit_sha": str(request.context.get("commit_sha", "")),
                    "reviewed_pr_number": request.context.get("pull_request_number"),
                    "evaluated_requirement_ids": requirement_ids,
                    "excluded_requirement_reasons": {},
                    "resolved_finding_ids": resolved,
                    "finding_resolution_evidence": {
                        finding_id: ["mock-evidence-1"] for finding_id in resolved
                    },
                }
            )
        return AgentResult(
            status=AgentRunStatus.SUCCESS,
            model="mock",
            turns=1,
            output=output,
            summary=f"{request.agent_id} completed the requested stage.",
            estimated_cost_usd=0,
        )

    @staticmethod
    def _resolvable_findings(request: AgentRequest, origin: str) -> list[str]:
        """Explicitly resolve only verified findings owned by this review type."""
        evidence = request.context.get("all_quality_evidence", {})
        if not isinstance(evidence, dict):
            evidence = {}
        candidates = evidence.get(
            "unresolved_findings", request.context.get("previous_findings", [])
        )
        if not isinstance(candidates, list):
            return []
        return [
            str(item["id"])
            for item in candidates
            if isinstance(item, dict)
            and item.get("origin_review_type") == origin
            and item.get("status") == "VERIFICATION_REQUIRED"
            and item.get("id")
        ]
