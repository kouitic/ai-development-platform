import asyncio
from pathlib import Path

import pytest

from ai_dev_platform.application.approval_service import REQUIREMENTS_STAGE, record_decision
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import load_config
from ai_dev_platform.domain.models import (
    AgentRequest,
    AgentResult,
    AgentRunStatus,
    Decision,
    DeploymentConfiguration,
    EvidenceReference,
    FindingStatus,
    TaskEvidence,
    TaskRecord,
    WorkflowState,
)
from ai_dev_platform.infrastructure.git import MockGitWorktree
from ai_dev_platform.infrastructure.github import MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.infrastructure.verification import MockVerificationRunner
from ai_dev_platform.providers.mock import MockAgentProvider

STRUCTURED_REQUIREMENTS = """```yaml
requirements:
  - id: BR-001
    type: BUSINESS
    description: Satisfy the Issue request
    acceptance_criteria:
      - Required tests and reviews pass
    required: true
```"""


def approved_deployment() -> DeploymentConfiguration:
    return DeploymentConfiguration(
        decision=Decision.PASS,
        summary="approved",
        evidence=[
            EvidenceReference(id="deployment-approval", kind="github", reference="mock-comment-1")
        ],
        deployment_target={"location": "private cloud"},
        environments=[{"name": "test"}, {"name": "production"}],
        human_approved=True,
        approver="human",
    )


class DecisionProvider(MockAgentProvider):
    def __init__(self, decisions: dict[int, Decision]) -> None:
        super().__init__()
        self.decisions = decisions

    async def execute(self, request: AgentRequest) -> AgentResult:
        result = await super().execute(request)
        call_index = len(self.requests) - 1
        decision = self.decisions.get(call_index)
        if decision is None:
            return result
        output = dict(result.output)
        output["decision"] = decision.value
        output["summary"] = f"decision={decision.value}"
        if decision in {Decision.REJECT, Decision.INSUFFICIENT_EVIDENCE}:
            output["findings"] = [
                {
                    "id": f"SYS-{call_index + 1:03d}",
                    "severity": "major",
                    "category": "correctness",
                    "requirement_ids": ["BR-001"],
                    "file": "src/example.py",
                    "line": 1,
                    "problem": "simulated defect",
                    "evidence": ["mock-evidence-1"],
                    "business_impact": "acceptance condition is not met",
                    "required_fix": "correct the implementation",
                    "acceptance_test": "rerun the deterministic test",
                    "blocking": True,
                }
            ]
        return result.model_copy(update={"output": output})


def setup_runner(
    root: Path,
    provider: MockAgentProvider | None = None,
    issue: int = 1,
    *,
    deployment_approved: bool = True,
) -> tuple[WorkflowRunner, SQLiteStateStore, TaskRecord, MockAgentProvider]:
    loaded = load_config(root)
    store = SQLiteStateStore(root / ".ai-dev" / "local" / f"state-{issue}.sqlite3")
    branch = f"ai/issue-{issue}-test"
    evidence = TaskEvidence(
        deployment_configuration=approved_deployment() if deployment_approved else None
    )
    task = store.create_task(
        TaskRecord(
            task_id=f"issue-{issue}",
            issue_number=issue,
            commit_sha="abcdef1234567890",
            branch=branch,
            evidence=evidence,
            context={
                "security_scan_results": ["no findings"],
                "static_analysis_results": ["passed"],
                "dependency_scan_results": ["passed"],
            },
        )
    )
    github = MockGitHubGateway()
    github.issues[issue] = {
        "title": "Test Issue",
        "body": STRUCTURED_REQUIREMENTS,
        "labels": [],
    }
    git = MockGitWorktree(branch=branch, files=["src/example.py"], diff_text="mock diff")
    selected = provider or MockAgentProvider()
    runner = WorkflowRunner(
        loaded.project,
        loaded.agents,
        selected,
        store,
        root=root,
        github=github,
        git=git,
        verification_runner=MockVerificationRunner(
            base_commit_sha=git.base_commit_sha,
            diff_text=git.diff_text,
        ),
        verification_policy=loaded.verification,
    )
    return runner, store, task, selected


def test_normal_flow_reaches_human_approval(initialized_project: Path) -> None:
    runner, store, task, provider = setup_runner(initialized_project)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert finished.iteration == 0
    assert finished.pull_request_number == 1
    assert len(provider.requests) == 7
    transitions = [
        event for event in store.list_events(task.task_id) if event["action"] == "state_transition"
    ]
    assert transitions[-1]["details"]["to"] == "HUMAN_APPROVAL_REQUIRED"


def test_ai_requirement_candidates_wait_for_formal_human_approval(
    initialized_project: Path,
) -> None:
    runner, store, task, _ = setup_runner(initialized_project, issue=91)
    assert isinstance(runner.github, MockGitHubGateway)
    runner.github.issues[91]["body"] = "Natural language request without structured YAML."

    waiting = asyncio.run(runner.run(task.task_id))
    assert waiting.state == WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED
    assert waiting.evidence.requirements_result is not None
    assert not waiting.evidence.requirements_result.human_approved

    approved = record_decision(
        store,
        issue_number=91,
        stage=REQUIREMENTS_STAGE,
        commit_sha=waiting.commit_sha,
        approver="human-reviewer",
        approved=True,
        gateway=runner.github,
    )
    assert approved.state == WorkflowState.DEPLOYMENT_CONFIGURATION
    assert approved.evidence.requirements_result is not None
    assert approved.evidence.requirements_result.human_approved

    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED


def test_missing_deployment_answers_stop_before_design(initialized_project: Path) -> None:
    runner, _, task, provider = setup_runner(
        initialized_project, issue=2, deployment_approved=False
    )
    stopped = asyncio.run(runner.run(task.task_id))
    assert stopped.state == WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED
    assert stopped.pending_human_decisions
    assert len(provider.requests) == 1


@pytest.mark.parametrize("failure_index", [4, 5, 6])
def test_each_quality_failure_is_reworked(initialized_project: Path, failure_index: int) -> None:
    provider = DecisionProvider({failure_index: Decision.REJECT})
    runner, _, task, _ = setup_runner(initialized_project, provider, issue=failure_index + 10)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert finished.iteration == 1


def test_mock_e2e_carries_system_finding_into_concrete_rework(
    initialized_project: Path,
) -> None:
    provider = DecisionProvider({4: Decision.REJECT})
    runner, _, task, _ = setup_runner(initialized_project, provider, issue=25)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert finished.pull_request_number == 1
    assert provider.requests[4].context["pull_request_diff"] == ""
    rework_context = provider.requests[5].context
    assert rework_context["previous_findings"][0]["id"] == "SYS-005"
    assert rework_context["rework_requirements"][0]["required_fix"]
    assert finished.evidence.unresolved_findings[0].status == FindingStatus.RESOLVED
    assert finished.evidence.system_reviews[-1].decision == Decision.PASS
    assert finished.evidence.business_reviews[-1].decision == Decision.PASS
    assert finished.evidence.qa_assessments[-1].decision == Decision.PASS


def test_insufficient_qa_evidence_is_not_pass(initialized_project: Path) -> None:
    provider = DecisionProvider({6: Decision.INSUFFICIENT_EVIDENCE})
    runner, _, task, _ = setup_runner(initialized_project, provider, issue=30)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert finished.iteration == 1


def test_four_failures_hit_three_rework_limit_and_block(initialized_project: Path) -> None:
    provider = DecisionProvider(
        {4: Decision.REJECT, 6: Decision.REJECT, 8: Decision.REJECT, 10: Decision.REJECT}
    )
    runner, _, task, _ = setup_runner(initialized_project, provider, issue=40)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.BLOCKED
    assert finished.iteration == 3


def test_invalid_agent_output_is_rejected(initialized_project: Path) -> None:
    invalid = AgentResult(status=AgentRunStatus.SUCCESS, output={"unexpected": "value"})
    runner, _, task, _ = setup_runner(initialized_project, MockAgentProvider([invalid]), issue=50)
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.FAILED


@pytest.mark.parametrize("status", [AgentRunStatus.ERROR, AgentRunStatus.TIMEOUT])
def test_provider_failure_stops_safely(initialized_project: Path, status: AgentRunStatus) -> None:
    failed = AgentResult(status=status, error_code="simulated_failure")
    runner, _, task, _ = setup_runner(
        initialized_project, MockAgentProvider([failed]), issue=60 + len(status.value)
    )
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.FAILED


def test_pause_at_boundary_and_resume(initialized_project: Path) -> None:
    runner, store, task, _ = setup_runner(initialized_project, issue=70)
    store.request_control(task.issue_number, "pause")
    paused = asyncio.run(runner.run(task.task_id))
    assert paused.state == WorkflowState.PAUSED
    assert paused.resume_state == WorkflowState.REQUIREMENTS_ANALYSIS
    store.request_control(task.issue_number, "resume")
    finished = asyncio.run(runner.run(task.task_id))
    assert finished.state == WorkflowState.HUMAN_APPROVAL_REQUIRED


def test_cancel_at_boundary(initialized_project: Path) -> None:
    runner, store, task, _ = setup_runner(initialized_project, issue=80)
    store.request_control(task.issue_number, "cancel")
    cancelled = asyncio.run(runner.run(task.task_id))
    assert cancelled.state == WorkflowState.FAILED


def test_per_task_budget_hard_stop_blocks(initialized_project: Path) -> None:
    expensive = AgentResult(
        status=AgentRunStatus.SUCCESS,
        model="mock",
        estimated_cost_usd=10,
        output={
            "decision": "PASS",
            "summary": "expensive",
            "evidence": ["mock-evidence"],
            "findings": [],
            "conditions": [],
            "business_requirements": ["BR-001"],
            "acceptance_criteria": ["AC-001"],
            "scope": [],
            "out_of_scope": [],
            "human_decisions": [],
        },
    )
    runner, _, task, _ = setup_runner(initialized_project, MockAgentProvider([expensive]), issue=90)
    blocked = asyncio.run(runner.run(task.task_id))
    assert blocked.state == WorkflowState.BLOCKED
    assert blocked.estimated_cost_usd == 10
