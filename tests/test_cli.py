from pathlib import Path

import pytest
from typer.testing import CliRunner

import ai_dev_platform.cli as cli_module
from ai_dev_platform.application.deployment import DEPLOYMENT_QUESTIONS
from ai_dev_platform.cli import app
from ai_dev_platform.domain.models import TaskRecord, WorkflowState
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore

runner = CliRunner()


def test_cli_init_validate_chat_run_approve_and_logs(tmp_path: Path) -> None:
    root = tmp_path / "cli-project"
    root.mkdir()
    initialized = runner.invoke(app, ["init", "cli-project", "--path", str(root)])
    assert initialized.exit_code == 0, initialized.output
    validated = runner.invoke(app, ["validate", "--path", str(root)])
    assert validated.exit_code == 0, validated.output

    chat = runner.invoke(app, ["chat", "在庫切れを通知したい", "--path", str(root)])
    assert chat.exit_code == 0, chat.output
    assert "Issue candidate" in chat.output

    commit = "abcdef1234567890"
    run = runner.invoke(
        app,
        ["run", "--issue", "12", "--commit-sha", commit, "--path", str(root)],
    )
    assert run.exit_code == 0, run.output
    state_store = SQLiteStateStore(root / ".ai-dev" / "local" / "state.sqlite3")
    assert (
        state_store.get_task_by_issue(12).state == WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED
    )

    for question in DEPLOYMENT_QUESTIONS:
        answered = runner.invoke(
            app,
            [
                "deployment-answer",
                "--issue",
                "12",
                "--question-id",
                question.id,
                "--answer",
                question.recommendation,
                "--answered-by",
                "tester",
                "--path",
                str(root),
            ],
        )
        assert answered.exit_code == 0, answered.output
    deployment_approved = runner.invoke(
        app,
        [
            "approve-deployment",
            "--issue",
            "12",
            "--approver",
            "tester",
            "--path",
            str(root),
        ],
    )
    assert deployment_approved.exit_code == 0, deployment_approved.output

    resumed = runner.invoke(app, ["run", "--issue", "12", "--path", str(root)])
    assert resumed.exit_code == 0, resumed.output
    assert state_store.get_task_by_issue(12).state == WorkflowState.HUMAN_APPROVAL_REQUIRED

    status = runner.invoke(app, ["status", "--issue", "12", "--path", str(root)])
    assert status.exit_code == 0
    assert state_store.get_task_by_issue(12).state == WorkflowState.HUMAN_APPROVAL_REQUIRED

    current_commit = (
        SQLiteStateStore(root / ".ai-dev" / "local" / "state.sqlite3")
        .get_task_by_issue(12)
        .commit_sha
    )
    wrong = runner.invoke(
        app,
        [
            "approve",
            "--issue",
            "12",
            "--stage",
            "human-approval",
            "--commit-sha",
            "0000000",
            "--approver",
            "tester",
            "--path",
            str(root),
        ],
    )
    assert wrong.exit_code == 1

    approved = runner.invoke(
        app,
        [
            "approve",
            "--issue",
            "12",
            "--stage",
            "human-approval",
            "--commit-sha",
            current_commit,
            "--approver",
            "tester",
            "--reason",
            "reviewed exact evidence",
            "--path",
            str(root),
        ],
    )
    assert approved.exit_code == 0, approved.output
    assert "COMPLETED" in approved.output
    assert "did not merge main" in approved.output

    logs = runner.invoke(app, ["logs", "--issue", "12", "--path", str(root)])
    assert logs.exit_code == 0, logs.output
    assert "state_transition" in logs.output


def test_cli_init_conflict_and_missing_task(tmp_path: Path) -> None:
    root = tmp_path / "conflict"
    root.mkdir()
    (root / "README.md").write_text("keep", encoding="utf-8")
    result = runner.invoke(app, ["init", "conflict", "--path", str(root)])
    assert result.exit_code == 1
    assert (root / "README.md").read_text(encoding="utf-8") == "keep"

    status = runner.invoke(app, ["status", "--issue", "999", "--path", str(root)])
    assert status.exit_code == 1


def test_cli_ask_does_not_change_state(initialized_project: Path) -> None:
    result = runner.invoke(
        app,
        ["ask", "qa", "品質証拠は何ですか", "--path", str(initialized_project)],
    )
    assert result.exit_code == 0, result.output
    assert "informational" in result.output


def test_review_and_qa_commands_share_quality_evidence(initialized_project: Path) -> None:
    common = [
        "--issue",
        "21",
        "--pr",
        "4",
        "--path",
        str(initialized_project),
    ]
    system = runner.invoke(app, ["review", *common, "--type", "system"])
    assert system.exit_code == 0, system.output
    business = runner.invoke(app, ["review", *common, "--type", "business"])
    assert business.exit_code == 0, business.output
    qa = runner.invoke(app, ["qa", *common])
    assert qa.exit_code == 0, qa.output
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "state.sqlite3")
    task = store.get_task_by_issue(21)
    assert task.state == WorkflowState.HUMAN_APPROVAL_REQUIRED
    assert task.evidence.system_reviews
    assert task.evidence.business_reviews
    assert task.evidence.qa_assessments


class FakePromptSession:
    def __init__(self, commands: list[str]) -> None:
        self.commands = iter(commands)

    def prompt(self, _: str) -> str:
        try:
            return next(self.commands)
        except StopIteration as exc:
            raise EOFError from exc


def test_interactive_read_commands_and_explicit_approval(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "state.sqlite3")
    store.create_task(
        TaskRecord(
            task_id="issue-99",
            issue_number=99,
            commit_sha="abcdef123456",
            state=WorkflowState.HUMAN_APPROVAL_REQUIRED,
            last_summary="quality evidence collected",
            estimated_cost_usd=1.25,
        )
    )
    commands = [
        "/help",
        "/pending",
        "/answer business-requirements approved-business-outcome",
        "/pending",
        "/sync-issue",
        "/technical-details",
        "/tasks",
        "/status 99",
        "/show 99",
        "/plan 99",
        "/reviews 99",
        "/quality 99",
        "/cost 99",
        "/diff 99",
        "/logs 99",
        "/agent qa quality evidence?",
        "@qa quality evidence?",
        "/approve 99 human-approval abcdef123456 tester",
        "/exit",
    ]
    monkeypatch.setattr(cli_module, "PromptSession", lambda: FakePromptSession(commands))
    cli_module._interactive_chat(initialized_project, 99)
    assert store.get_task_by_issue(99).state == WorkflowState.COMPLETED


def test_interactive_rejection_returns_to_rework(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = SQLiteStateStore(initialized_project / ".ai-dev" / "local" / "state.sqlite3")
    store.create_task(
        TaskRecord(
            task_id="issue-100",
            issue_number=100,
            commit_sha="abcdef123456",
            state=WorkflowState.HUMAN_APPROVAL_REQUIRED,
        )
    )
    commands = [
        "/reject 100 human-approval abcdef123456 tester more evidence is required",
        "/exit",
    ]
    monkeypatch.setattr(cli_module, "PromptSession", lambda: FakePromptSession(commands))
    cli_module._interactive_chat(initialized_project, 100)
    assert store.get_task_by_issue(100).state == WorkflowState.REWORK_REQUIRED
