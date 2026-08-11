"""`ai-dev` terminal interface."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Annotated, Any

import typer
from prompt_toolkit import PromptSession
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ai_dev_platform.application.approval_service import record_decision
from ai_dev_platform.application.conversation import (
    merge_confirmed_decisions,
    record_conversation_answer,
    render_confirmed_decisions,
    render_issue_body,
    start_conversation_session,
    structure_request,
    unanswered_decisions,
)
from ai_dev_platform.application.deployment import (
    approve_deployment_configuration,
    record_answer,
    start_deployment_session,
    unanswered_questions,
)
from ai_dev_platform.application.doctor import run_doctor
from ai_dev_platform.application.init_service import InitConflictError, initialize_project
from ai_dev_platform.application.issue_preflight import (
    render_approval_templates,
    validate_approved_issue,
)
from ai_dev_platform.application.package_service import package_source, verify_source_package
from ai_dev_platform.application.provider_preflight import write_provider_preflight_report
from ai_dev_platform.application.quality_gate import (
    run_integrated_quality_gates,
    run_quality_gate,
)
from ai_dev_platform.application.requirements import requirements_digest
from ai_dev_platform.application.validator import validate_project
from ai_dev_platform.application.workflow_runner import WorkflowRunner
from ai_dev_platform.config.loader import ConfigError, load_config
from ai_dev_platform.domain.models import (
    AgentRequest,
    ChangedFile,
    ProviderPreflightReport,
    PullRequestData,
    StageResult,
    TaskEvidence,
    TaskRecord,
    WorkflowState,
)
from ai_dev_platform.infrastructure.git import MockGitWorktree, SafeGitWorktree
from ai_dev_platform.infrastructure.github import GhCliGateway, GitHubError, MockGitHubGateway
from ai_dev_platform.infrastructure.state_store import (
    SQLiteStateStore,
    TaskNotFoundError,
)
from ai_dev_platform.infrastructure.verification import (
    LocalVerificationRunner,
    MockVerificationRunner,
    VerificationError,
    read_verification_result,
    write_verification_result,
)
from ai_dev_platform.providers.claude import ClaudeAgentProvider
from ai_dev_platform.providers.factory import create_provider
from ai_dev_platform.providers.mock import MockAgentProvider
from ai_dev_platform.security.scanner import scan_tree

app = typer.Typer(
    name="ai-dev",
    help="Human-governed AI development workflow CLI.",
    no_args_is_help=True,
)
console = Console()
STAGE_RESULT_SCHEMA: dict[str, Any] = StageResult.model_json_schema()


def _root(path: Path) -> Path:
    return path.resolve()


def _store(root: Path) -> SQLiteStateStore:
    return SQLiteStateStore(root / ".ai-dev" / "local" / "state.sqlite3")


def _current_sha(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    value = result.stdout.strip()
    return (
        value
        if result.returncode == 0 and len(value) >= 7
        else "mock000000000000000000000000000000000000"
    )


def _gateway(root: Path, gateway_name: str) -> MockGitHubGateway | GhCliGateway:
    selected = os.getenv("AI_DEV_GITHUB_GATEWAY", gateway_name).lower()
    if selected not in {"gh", "mock"}:
        raise ValueError("AI_DEV_GITHUB_GATEWAY must be gh or mock")
    return GhCliGateway(root) if selected == "gh" else MockGitHubGateway()


def _seed_mock_gateway(
    gateway: MockGitHubGateway | GhCliGateway,
    *,
    issue_number: int,
    task: TaskRecord | None = None,
    pull_request_number: int | None = None,
    commit_sha: str | None = None,
) -> None:
    """Create deterministic offline GitHub state without pretending it is external."""
    if not isinstance(gateway, MockGitHubGateway):
        return
    gateway.issues[issue_number] = {
        "title": f"Mock Issue #{issue_number}",
        "body": f"""```yaml
requirements:
  - id: BR-001
    type: BUSINESS
    description: Complete the offline Mock request for Issue #{issue_number}
    acceptance_criteria:
      - Trusted verification and required reviews pass
    required: true
```""",
        "labels": ["ai:ready"],
    }
    pr_number = pull_request_number or (task.pull_request_number if task is not None else None)
    if pr_number is None:
        return
    branch = task.branch if task is not None and task.branch else f"ai/issue-{issue_number}-mock"
    sha = commit_sha or (task.commit_sha if task is not None else "m" * 40)
    gateway.pull_requests[pr_number] = PullRequestData(
        number=pr_number,
        title=f"Mock PR #{pr_number}",
        head_branch=branch,
        base_branch="main",
        head_sha=sha,
        url=f"mock://pulls/{pr_number}",
    ).model_dump(mode="json")
    gateway.changed_files[pr_number] = [ChangedFile(path="src/mock-change.py", status="modified")]
    gateway.pull_request_diffs[pr_number] = "mock diff"


def _load(root: Path) -> Any:
    try:
        return load_config(root)
    except ConfigError as exc:
        console.print(f"[red]設定エラー:[/red] {exc}")
        raise typer.Exit(2) from exc


def _task_table(tasks: list[TaskRecord]) -> Table:
    table = Table(title="AI development tasks")
    table.add_column("Issue")
    table.add_column("State")
    table.add_column("Iteration")
    table.add_column("Commit")
    for task in tasks:
        table.add_row(
            f"#{task.issue_number}",
            task.state.value,
            str(task.iteration),
            f"{task.commit_sha[:12]} (${task.estimated_cost_usd:.2f} est.)",
        )
    return table


@app.command("init")
def init_command(
    project_name: Annotated[str, typer.Argument(help="Project name stored in project.yaml")],
    path: Annotated[Path, typer.Option("--path", help="Target repository directory")] = Path("."),
) -> None:
    """Initialize a repository without overwriting any existing file."""
    try:
        result = initialize_project(_root(path), project_name)
    except InitConflictError as exc:
        console.print("[red]初期化を中止しました。既存ファイルとは競合しません。[/red]")
        for conflict in exc.conflicts:
            console.print(f"- {conflict.as_posix()}")
        raise typer.Exit(1) from exc
    console.print(f"[green]{len(result.created)} files created.[/green]")
    console.print("Next: ai-dev doctor, then ai-dev validate")


@app.command("validate")
def validate_command(
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Validate config, workflow, permissions, secrets, and data policy."""
    issues = validate_project(_root(path))
    if not issues:
        console.print("[green]Validation passed.[/green]")
        return
    table = Table(title="Validation findings")
    table.add_column("Severity")
    table.add_column("Code")
    table.add_column("Path")
    table.add_column("Message")
    for issue in issues:
        style = "red" if issue.severity == "error" else "yellow"
        table.add_row(f"[{style}]{issue.severity}[/{style}]", issue.code, issue.path, issue.message)
    console.print(table)
    if any(issue.severity == "error" for issue in issues):
        raise typer.Exit(1)


@app.command("doctor")
def doctor_command(
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Diagnose local and external prerequisites without showing secret values."""
    checks = run_doctor(_root(path))
    table = Table(title="ai-dev doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in checks:
        style = {"ok": "green", "warning": "yellow", "error": "red"}.get(check.status, "dim")
        table.add_row(check.name, f"[{style}]{check.status}[/{style}]", check.detail)
    console.print(table)
    if any(check.status == "error" for check in checks):
        raise typer.Exit(1)


def _show_draft(request: str, *, technical: bool = False) -> str:
    draft = structure_request(request)
    console.print(
        Panel(f"[bold]{draft.title}[/bold]\n\n{draft.business_impact}", title="Issue candidate")
    )
    console.print("[bold]人間判断が必要な事項[/bold]")
    for decision in draft.human_decisions_required:
        console.print(f"- {decision}")
    if technical:
        console.print_json(data=draft.model_dump(mode="json"))
    return render_issue_body(draft)


def _interactive_chat(root: Path, issue: int | None, *, ai_mode: bool = False) -> None:
    session: PromptSession[str] = PromptSession()
    technical = False
    loaded = _load(root)
    store = _store(root)
    conversation_id = (
        f"{loaded.project.project.name}:issue-{issue}:conversation"
        if issue is not None
        else f"{loaded.project.project.name}:project:conversation"
    )
    try:
        conversation = store.get_conversation_session(conversation_id)
    except TaskNotFoundError:
        conversation = store.save_conversation_session(
            start_conversation_session(loaded.project.project.name, issue)
        )
    console.print("/help でコマンド、/exit で終了します。")
    while True:
        try:
            text = session.prompt("ai-dev> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if not text:
            continue
        if text == "/exit":
            return
        if text == "/help":
            console.print(
                "/status [issue], /tasks, /show|plan|reviews|quality|cost <issue>, "
                "/pause|resume|cancel <issue>, /diff <issue>, /logs <issue>, "
                "/agent <agent-id> <question>, @<agent-id> <question>, "
                "/pending, /answer <decision-id> <answer>, /sync-issue, "
                "/approve <issue> <stage> <sha> <approver>, "
                "/reject <issue> <stage> <sha> <approver> <reason>, "
                "/technical-details, /exit"
            )
            continue
        parts = text.split()
        command = parts[0]
        if command == "/pending":
            pending = unanswered_decisions(conversation)
            if not pending:
                console.print("未回答の人間判断はありません。")
            for question in pending:
                console.print(
                    f"- {question.id}: {question.question}\n"
                    f"  推奨案: {question.recommendation}\n"
                    f"  理由: {question.recommendation_reason}"
                )
            continue
        if command == "/answer" and len(parts) >= 3:
            try:
                conversation = record_conversation_answer(
                    store,
                    conversation,
                    question_id=parts[1],
                    answer=" ".join(parts[2:]),
                    answered_by="interactive-human",
                )
                console.print("回答を保存しました。回答済み項目は再質問しません。")
            except ValueError as exc:
                console.print(f"回答を保存できません: {exc}")
            continue
        if command == "/sync-issue":
            if issue is None:
                console.print("Issue単位のsessionでだけ同期できます。")
                continue
            try:
                confirmed = render_confirmed_decisions(conversation)
                gateway = _gateway(root, loaded.project.github.gateway)
                _seed_mock_gateway(gateway, issue_number=issue)
                current = gateway.get_issue(issue)
                gateway.update_issue(issue, merge_confirmed_decisions(current.body, confirmed))
                console.print("確定した回答だけをGitHub Issueへ同期しました。")
            except ValueError as exc:
                console.print(f"同期できません: {exc}")
            continue
        if text == "/technical-details":
            technical = not technical
            console.print(f"Technical details: {'on' if technical else 'off'}")
            continue
        if command in {"/status", "/show", "/tasks", "/plan"}:
            store = _store(root)
            if command == "/tasks":
                console.print(_task_table(store.list_tasks()))
            else:
                selected = int(parts[1]) if len(parts) > 1 else issue
                if selected is None:
                    console.print("Issue number is required.")
                else:
                    try:
                        console.print(_task_table([store.get_task_by_issue(selected)]))
                    except TaskNotFoundError:
                        console.print("Task not found.")
            continue
        if command in {"/reviews", "/quality", "/cost"} and len(parts) == 2:
            store = _store(root)
            try:
                task = store.get_task_by_issue(int(parts[1]))
            except TaskNotFoundError:
                console.print("Task not found.")
                continue
            if command == "/cost":
                console.print(f"Estimated task cost: ${task.estimated_cost_usd:.2f}")
            elif command == "/quality":
                summary = task.last_summary or "none"
                console.print(f"State: {task.state.value}\nLast validated summary: {summary}")
            else:
                review_states = {"SYSTEM_REVIEW", "BUSINESS_REVIEW", "QA_ASSESSMENT"}
                events = [
                    event
                    for event in store.list_events(task.task_id)
                    if event["details"].get("from") in review_states
                    or event["details"].get("to") in review_states
                ]
                console.print_json(data=events)
            continue
        if command == "/diff" and len(parts) == 2:
            diff = subprocess.run(
                ["git", "diff", "--stat"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            console.print(diff.stdout.strip() or "No local diff summary is available.")
            continue
        if command in {"/pause", "/resume", "/cancel"} and len(parts) == 2:
            _store(root).request_control(int(parts[1]), command.removeprefix("/"))
            console.print(f"{command} requested at the next safe stage boundary.")
            continue
        if command == "/logs" and len(parts) == 2:
            task = _store(root).get_task_by_issue(int(parts[1]))
            console.print_json(data=_store(root).list_events(task.task_id))
            continue
        if command == "/approve" and len(parts) == 5:
            try:
                existing = _store(root).get_task_by_issue(int(parts[1]))
                loaded = _load(root)
                gateway = _gateway(root, loaded.project.github.gateway)
                _seed_mock_gateway(gateway, issue_number=int(parts[1]), task=existing)
                updated = record_decision(
                    _store(root),
                    issue_number=int(parts[1]),
                    stage=parts[2],
                    commit_sha=parts[3],
                    approver=parts[4],
                    approved=True,
                    pull_request_number=existing.pull_request_number,
                    gateway=gateway,
                )
                console.print(_task_table([updated]))
            except (TaskNotFoundError, ValueError) as exc:
                console.print(f"Approval rejected: {exc}")
            continue
        if command == "/reject" and len(parts) >= 6:
            try:
                existing = _store(root).get_task_by_issue(int(parts[1]))
                loaded = _load(root)
                gateway = _gateway(root, loaded.project.github.gateway)
                _seed_mock_gateway(gateway, issue_number=int(parts[1]), task=existing)
                updated = record_decision(
                    _store(root),
                    issue_number=int(parts[1]),
                    stage=parts[2],
                    commit_sha=parts[3],
                    approver=parts[4],
                    approved=False,
                    reason=" ".join(parts[5:]),
                    pull_request_number=existing.pull_request_number,
                    gateway=gateway,
                )
                console.print(_task_table([updated]))
            except (TaskNotFoundError, ValueError) as exc:
                console.print(f"Rejection was not recorded: {exc}")
            continue
        if command == "/agent" and len(parts) >= 3:
            _ask_informational(root, parts[1], " ".join(parts[2:]))
            continue
        if command.startswith("@") and len(parts) >= 2:
            _ask_informational(root, command.removeprefix("@"), " ".join(parts[1:]))
            continue
        if ai_mode:
            _ask_informational(root, "conversation", text)
        _show_draft(text, technical=technical)


def _ask_informational(root: Path, agent_id: str, question: str) -> None:
    """Display an informational agent answer without changing workflow state."""
    loaded = _load(root)
    if agent_id not in loaded.agents:
        console.print("Undefined agent.")
        return
    agent = loaded.agents[agent_id]
    result = asyncio.run(
        create_provider(loaded.project, root=root).execute(
            AgentRequest(
                agent_id=agent_id,
                prompt=question,
                system_prompt=agent.system_prompt,
                model=agent.model,
                max_turns=agent.max_turns,
                timeout_seconds=loaded.project.workflow.timeout_minutes * 60,
                max_budget_usd=loaded.project.budget.per_task.stop_usd,
                allowed_tools=agent.available_tools,
                forbidden_tools=agent.forbidden_tools,
                output_schema=STAGE_RESULT_SCHEMA,
            )
        )
    )
    console.print_json(data=result.model_dump(mode="json"))
    console.print("This answer is informational and did not change formal state.")


@app.command("chat")
def chat_command(
    request: Annotated[str | None, typer.Argument(help="Natural-language request")] = None,
    issue: Annotated[int | None, typer.Option("--issue", help="Existing Issue context")] = None,
    create_issue: Annotated[
        bool, typer.Option("--create-issue", help="Create the structured Issue explicitly")
    ] = False,
    change_for: Annotated[
        int | None,
        typer.Option("--change-for", help="Create a follow-up change for a completed Issue"),
    ] = None,
    ai: Annotated[
        bool,
        typer.Option("--ai", help="Use the configured AgentProvider for interactive guidance"),
    ] = False,
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Structure a request or start an interactive terminal conversation."""
    root = _root(path)
    loaded = _load(root)
    if request is None:
        _interactive_chat(root, issue, ai_mode=ai)
        return
    draft = structure_request(request)
    if change_for is not None:
        draft = draft.model_copy(update={"title": f"Change for #{change_for}: {draft.title}"})
    body = _show_draft(request)
    if create_issue:
        gateway = _gateway(root, loaded.project.github.gateway)
        number = gateway.create_issue(draft.title, body, ["ai:ready", "type:feature"])
        console.print(f"[green]Issue #{number} created.[/green]")


@app.command("ask")
def ask_command(
    agent_id: Annotated[str, typer.Argument(help="Configured agent id")],
    question: Annotated[str, typer.Argument(help="Question; this never changes formal state")],
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Ask an agent for information without changing reviews or workflow state."""
    root = _root(path)
    loaded = _load(root)
    if agent_id not in loaded.agents:
        console.print("[red]Undefined agent.[/red]")
        raise typer.Exit(1)
    _ask_informational(root, agent_id, question)


@app.command("run")
def run_command(
    issue: Annotated[int, typer.Option("--issue", help="GitHub Issue number")],
    commit_sha: Annotated[
        str | None, typer.Option("--commit-sha", help="Exact commit evaluated by the workflow")
    ] = None,
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Run or resume one Issue until a safe terminal or human gate."""
    root = _root(path)
    loaded = _load(root)
    store = _store(root)
    gateway = _gateway(root, loaded.project.github.gateway)
    approved_issue = None
    if isinstance(gateway, GhCliGateway):
        try:
            approved_issue = validate_approved_issue(gateway, issue)
        except (ValueError, GitHubError) as exc:
            console.print(f"[red]承認済みIssueの事前検証に失敗しました: {exc}[/red]")
            raise typer.Exit(2) from exc
    try:
        provider = create_provider(
            loaded.project,
            root=root,
            purpose="development",
            issue_number=issue,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    try:
        task = store.get_task_by_issue(issue)
    except TaskNotFoundError:
        _seed_mock_gateway(gateway, issue_number=issue)
        issue_data = (
            approved_issue.issue if approved_issue is not None else gateway.get_issue(issue)
        )
        branch = gateway.create_branch(
            issue, "ai-development", loaded.project.github.default_branch
        )
        evidence = TaskEvidence()
        if approved_issue is not None:
            evidence = evidence.model_copy(
                update={
                    "requirements_approval": approved_issue.requirements_approval,
                    "deployment_configuration": approved_issue.deployment_configuration,
                }
            )
        task = TaskRecord(
            task_id=f"issue-{issue}",
            issue_number=issue,
            commit_sha=commit_sha or _current_sha(root),
            branch=branch,
            evidence=evidence,
            context={
                "interaction_mode": loaded.project.interaction.mode,
                "issue_reference": {
                    "number": issue_data.number,
                    "url": issue_data.url,
                    "labels": issue_data.labels,
                },
                "security_scan_results": [
                    "local secret scan passed"
                    if not scan_tree(root)
                    else "local secret scan failed"
                ],
                "available_data_classifications": ["PUBLIC_DUMMY", "SYNTHETIC"],
            },
        )
        store.create_task(task)
        store.append_event(task.task_id, "github", "branch_created", "success", {"branch": branch})
    if approved_issue is not None:
        persisted_requirements = task.evidence.requirements_result
        if persisted_requirements is not None and requirements_digest(
            persisted_requirements.requirements
        ) != requirements_digest(approved_issue.requirements):
            console.print("[red]保存済みタスクの要件は現在の承認済みIssueと一致しません。[/red]")
            raise typer.Exit(2)
        evidence = task.evidence.model_copy(
            update={
                "requirements_approval": approved_issue.requirements_approval,
                "deployment_configuration": approved_issue.deployment_configuration,
            },
            deep=True,
        )
        task = store.save_task(task.model_copy(update={"evidence": evidence}))
    _seed_mock_gateway(gateway, issue_number=issue, task=task)
    if commit_sha and commit_sha != task.commit_sha:
        task = store.save_task(
            task.model_copy(
                update={
                    "commit_sha": commit_sha,
                    "state": WorkflowState.REQUIREMENTS_ANALYSIS,
                    "iteration": 0,
                }
            )
        )
        store.append_event(task.task_id, "human", "commit_changed", "approval_invalidated")
    git_gateway: MockGitWorktree | SafeGitWorktree
    verification_runner: MockVerificationRunner | LocalVerificationRunner
    if isinstance(gateway, MockGitHubGateway):
        git_gateway = MockGitWorktree(
            branch=task.branch,
            files=["src/mock-change.py"],
            diff_text="mock diff for offline E2E",
        )
        verification_runner = MockVerificationRunner(
            base_commit_sha=git_gateway.base_commit_sha,
            diff_text=git_gateway.diff_text,
        )
    else:
        developer = loaded.agents["developer"]
        git_gateway = SafeGitWorktree(
            root,
            writable_patterns=developer.writable_paths,
            protected_patterns=loaded.project.protected_paths,
        )
        if git_gateway.current_branch() != task.branch:
            git_gateway.checkout_issue_branch(task.branch)
        verification_runner = LocalVerificationRunner()
    if loaded.verification is None:
        console.print("[red]Verification policy is missing.[/red]")
        raise typer.Exit(2)
    runner = WorkflowRunner(
        loaded.project,
        loaded.agents,
        provider,
        store,
        root=root,
        github=gateway,
        git=git_gateway,
        verification_runner=verification_runner,
        verification_policy=loaded.verification,
        stop_after_pull_request=isinstance(gateway, GhCliGateway),
    )
    task = asyncio.run(runner.run(task.task_id))
    console.print(_task_table([task]))
    if task.state == WorkflowState.HUMAN_APPROVAL_REQUIRED:
        console.print(
            "[yellow]両レビューとQA評価が完了しました。[/yellow] "
            "対象Issue、human-approval工程、commit SHAを指定した人間承認が必要です。"
        )
    elif task.state == WorkflowState.REQUIREMENTS_APPROVAL_REQUIRED:
        console.print(
            "[yellow]AIが生成した要件候補の人間承認が必要です。[/yellow] "
            "対象Issue、requirements工程、commit SHAを指定してapproveしてください。"
        )
    elif task.state == WorkflowState.DEPLOYMENT_CONFIGURATION_REQUIRED:
        console.print(
            "[yellow]デプロイ先・環境構成の回答と人間承認が必要です。[/yellow] "
            "deployment-questions で未回答項目を確認してください。"
        )
    elif task.state == WorkflowState.PAUSED and task.pull_request_number is not None:
        console.print(
            "[yellow]PRを作成しました。[/yellow] "
            "以後のSystem Review、Business Review、QAはPR Workflowが独立して実行します。"
        )


@app.command("issue-approval-template")
def issue_approval_template_command(
    issue: Annotated[int, typer.Option("--issue", help="GitHub Issue number")],
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Display digest-bound Japanese approval comments without approving the Issue."""
    root = _root(path)
    loaded = _load(root)
    gateway = _gateway(root, loaded.project.github.gateway)
    if not isinstance(gateway, GhCliGateway):
        console.print("[red]承認コメント生成には実GitHub gatewayが必要です。[/red]")
        raise typer.Exit(2)
    try:
        console.print(render_approval_templates(gateway, issue))
    except (ValueError, GitHubError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc


@app.command("issue-preflight")
def issue_preflight_command(
    issue: Annotated[int, typer.Option("--issue", help="GitHub Issue number")],
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Validate the approved Issue before any branch or Agent execution."""
    root = _root(path)
    loaded = _load(root)
    gateway = _gateway(root, loaded.project.github.gateway)
    if not isinstance(gateway, GhCliGateway):
        console.print("[red]Issue事前検証には実GitHub gatewayが必要です。[/red]")
        raise typer.Exit(2)
    try:
        approved = validate_approved_issue(gateway, issue)
    except (ValueError, GitHubError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(2) from exc
    console.print(
        f"承認済みIssue #{issue}: "
        f"要件={approved.requirements_approval.requirements_digest}, "
        f"環境構成={approved.deployment_digest}"
    )


def _deployment_session(store: SQLiteStateStore, project_name: str, issue: int) -> Any:
    session_id = f"{project_name}:issue-{issue}:deployment"
    try:
        return store.get_conversation_session(session_id)
    except TaskNotFoundError:
        session = start_deployment_session(project_name, issue)
        return store.save_conversation_session(session)


@app.command("deployment-questions")
def deployment_questions_command(
    issue: Annotated[int, typer.Option("--issue")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Show only unanswered business-facing deployment questions."""
    root = _root(path)
    loaded = _load(root)
    session = _deployment_session(_store(root), loaded.project.project.name, issue)
    questions = unanswered_questions(session)
    if not questions:
        console.print("[green]All deployment questions are answered.[/green]")
        return
    for question in questions:
        console.print(Panel(question.question, title=question.id))
        console.print(f"メリット: {' / '.join(question.benefits)}")
        console.print(f"デメリット: {' / '.join(question.drawbacks)}")
        console.print(f"推奨案: {question.recommendation}")
        console.print(f"推奨理由: {question.recommendation_reason}")


@app.command("deployment-answer")
def deployment_answer_command(
    issue: Annotated[int, typer.Option("--issue")],
    question_id: Annotated[str, typer.Option("--question-id")],
    answer: Annotated[str, typer.Option("--answer")],
    answered_by: Annotated[str, typer.Option("--answered-by")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Persist one sanitized human deployment answer."""
    root = _root(path)
    loaded = _load(root)
    store = _store(root)
    session = _deployment_session(store, loaded.project.project.name, issue)
    try:
        updated = record_answer(
            store,
            session,
            question_id=question_id,
            answer=answer,
            answered_by=answered_by,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(
        f"[green]Answer recorded.[/green] Remaining: {len(unanswered_questions(updated))}"
    )


@app.command("approve-deployment")
def approve_deployment_command(
    issue: Annotated[int, typer.Option("--issue")],
    approver: Annotated[str, typer.Option("--approver")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Approve complete deployment answers after recording the decision in GitHub."""
    root = _root(path)
    loaded = _load(root)
    store = _store(root)
    session = _deployment_session(store, loaded.project.project.name, issue)
    if unanswered_questions(session):
        console.print("[red]All deployment questions must be answered first.[/red]")
        raise typer.Exit(1)
    try:
        task = store.get_task_by_issue(issue)
        gateway = _gateway(root, loaded.project.github.gateway)
        _seed_mock_gateway(gateway, issue_number=issue, task=task)
        record_id = gateway.add_issue_comment(
            issue,
            f"Deployment configuration approved by {approver} for commit {task.commit_sha}.",
        )
        updated = approve_deployment_configuration(
            store,
            session,
            approver=approver,
            github_record_id=record_id,
        )
    except (TaskNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table([updated]))


def _quality_command(
    issue: int,
    pr: int,
    stage: WorkflowState,
    verification_result: Path | None,
    path: Path,
) -> None:
    root = _root(path)
    loaded = _load(root)
    store = _store(root)
    gateway = _gateway(root, loaded.project.github.gateway)
    try:
        existing = store.get_task_by_issue(issue)
    except TaskNotFoundError:
        existing = None
    _seed_mock_gateway(
        gateway,
        issue_number=issue,
        task=existing,
        pull_request_number=pr,
        commit_sha=existing.commit_sha if existing is not None else _current_sha(root),
    )
    pull_request = gateway.get_pull_request(pr)
    if isinstance(gateway, MockGitHubGateway):
        if loaded.verification is None:
            raise typer.Exit(2)
        verification = MockVerificationRunner(
            diff_text=gateway.get_pull_request_diff(pr)
        ).run_committed(
            root,
            [item.path for item in gateway.get_changed_files(pr)],
            loaded.verification,
            base_commit_sha="c" * 40,
            commit_sha=pull_request.head_sha,
        )
    elif verification_result is not None:
        try:
            verification = read_verification_result(verification_result)
        except VerificationError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from exc
    else:
        console.print("[red]Real GitHub review requires --verification-result.[/red]")
        raise typer.Exit(2)
    try:
        task = run_quality_gate(
            loaded,
            create_provider(loaded.project, root=root),
            store,
            gateway,
            root,
            issue_number=issue,
            pull_request_number=pr,
            stage=stage,
            verification=verification,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table([task]))
    failure_states = {
        WorkflowState.REWORK_REQUIRED,
        WorkflowState.BLOCKED,
        WorkflowState.FAILED,
        WorkflowState.QA_CONDITIONAL_APPROVAL_REQUIRED,
    }
    if task.state in failure_states:
        raise typer.Exit(1)


@app.command("review")
def review_command(
    issue: Annotated[int, typer.Option("--issue")],
    pr: Annotated[int, typer.Option("--pr")],
    review_type: Annotated[str, typer.Option("--type")],
    verification_result: Annotated[
        Path | None,
        typer.Option("--verification-result", help="Digest-protected host verification JSON"),
    ] = None,
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Run one exact-PR system or business AI review and publish its Check."""
    stages = {
        "system": WorkflowState.SYSTEM_REVIEW,
        "business": WorkflowState.BUSINESS_REVIEW,
    }
    if review_type not in stages:
        console.print("[red]--type must be system or business.[/red]")
        raise typer.Exit(2)
    _quality_command(
        issue,
        pr,
        stages[review_type],
        verification_result,
        path,
    )


@app.command("qa")
def qa_command(
    issue: Annotated[int, typer.Option("--issue")],
    pr: Annotated[int, typer.Option("--pr")],
    verification_result: Annotated[
        Path | None,
        typer.Option("--verification-result", help="Digest-protected host verification JSON"),
    ] = None,
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Integrate persisted tests and both reviews into the final AI QA gate."""
    _quality_command(
        issue,
        pr,
        WorkflowState.QA_ASSESSMENT,
        verification_result,
        path,
    )


@app.command("verify-commit")
def verify_commit_command(
    pr: Annotated[int, typer.Option("--pr")],
    base_sha: Annotated[str, typer.Option("--base-sha")],
    head_sha: Annotated[str, typer.Option("--head-sha")],
    output: Annotated[Path, typer.Option("--output")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Run host verification for one exact clean PR head and write protected evidence."""
    root = _root(path)
    loaded = _load(root)
    if loaded.verification is None:
        raise typer.Exit(2)
    gateway = _gateway(root, loaded.project.github.gateway)
    _seed_mock_gateway(
        gateway,
        issue_number=1,
        pull_request_number=pr,
        commit_sha=head_sha,
    )
    pull_request = gateway.get_pull_request(pr)
    if pull_request.head_sha != head_sha:
        console.print("[red]Pull Request head SHA mismatch.[/red]")
        raise typer.Exit(1)
    files = [item.path for item in gateway.get_changed_files(pr)]
    runner = (
        MockVerificationRunner(diff_text=gateway.get_pull_request_diff(pr))
        if isinstance(gateway, MockGitHubGateway)
        else LocalVerificationRunner()
    )
    try:
        result = runner.run_committed(
            root,
            files,
            loaded.verification,
            base_commit_sha=base_sha,
            commit_sha=head_sha,
        )
        write_verification_result(output, result)
    except VerificationError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    if result.overall_status.value != "PASS":
        console.print("[red]Host verification failed.[/red]")
        raise typer.Exit(1)
    console.print(f"[green]Trusted verification written:[/green] {output.resolve()}")


@app.command("provider-preflight")
def provider_preflight_command(
    artifact: Annotated[
        Path,
        typer.Option("--artifact", help="Sanitized provider preflight JSON"),
    ] = Path(".ai-dev/local/quality-artifacts/provider-preflight.json"),
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Probe the configured provider without retaining prompts or response content."""
    root = _root(path)
    loaded = _load(root)
    try:
        provider = create_provider(loaded.project, root=root)
        if isinstance(provider, MockAgentProvider):
            report = ProviderPreflightReport(
                provider="mock",
                commit_sha=_current_sha(root),
                overall_status="SKIPPED",
            )
        elif isinstance(provider, ClaudeAgentProvider):
            stages = asyncio.run(
                provider.preflight(
                    timeout_seconds=loaded.project.workflow.timeout_minutes * 60,
                    max_budget_usd=loaded.project.budget.per_task.stop_usd,
                )
            )
            report = ProviderPreflightReport(
                provider="claude",
                commit_sha=_current_sha(root),
                overall_status=(
                    "ERROR" if any(stage.status == "ERROR" for stage in stages) else "PASS"
                ),
                stages=stages,
            )
        else:
            raise ValueError("configured provider does not support preflight")
        destination = write_provider_preflight_report(root, artifact, report)
    except ValueError as exc:
        console.print(f"[red]Provider preflight configuration failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    if report.overall_status == "ERROR":
        failed = next(stage for stage in report.stages if stage.status == "ERROR")
        console.print(
            f"[red]Provider preflight failed:[/red] stage={failed.stage}; code={failed.error_code}"
        )
        raise typer.Exit(1)
    console.print(f"[green]Provider preflight {report.overall_status}:[/green] {destination}")


@app.command("quality-gates")
def quality_gates_command(
    issue: Annotated[int, typer.Option("--issue")],
    pr: Annotated[int, typer.Option("--pr")],
    base_sha: Annotated[str, typer.Option("--base-sha")],
    head_sha: Annotated[str, typer.Option("--head-sha")],
    artifact_directory: Annotated[Path, typer.Option("--artifact-directory")] = Path(
        ".ai-dev/local/quality-artifacts"
    ),
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Run CI verification, System, Business, and QA once in one ordered process."""
    root = _root(path)
    loaded = _load(root)
    if loaded.verification is None:
        raise typer.Exit(2)
    gateway = _gateway(root, loaded.project.github.gateway)
    _seed_mock_gateway(
        gateway,
        issue_number=issue,
        pull_request_number=pr,
        commit_sha=head_sha,
    )
    pull_request = gateway.get_pull_request(pr)
    if pull_request.head_sha != head_sha:
        console.print("[red]Pull Request head SHA mismatch.[/red]")
        raise typer.Exit(1)
    changed_files = [item.path for item in gateway.get_changed_files(pr)]
    verification_runner = (
        MockVerificationRunner(diff_text=gateway.get_pull_request_diff(pr))
        if isinstance(gateway, MockGitHubGateway)
        else LocalVerificationRunner()
    )
    try:
        verification = verification_runner.run_committed(
            root,
            changed_files,
            loaded.verification,
            base_commit_sha=base_sha,
            commit_sha=head_sha,
        )
        verification_path = (root / artifact_directory / "verification.json").resolve()
        write_verification_result(verification_path, verification)
        verification = read_verification_result(verification_path)
        task = run_integrated_quality_gates(
            loaded,
            create_provider(loaded.project, root=root),
            _store(root),
            gateway,
            root,
            issue_number=issue,
            pull_request_number=pr,
            verification=verification,
            artifact_directory=(root / artifact_directory).resolve(),
        )
    except (ValueError, VerificationError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table([task]))


@app.command("package-source")
def package_source_command(
    output: Annotated[Path | None, typer.Option("--output")] = None,
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Create a manifest-bound source ZIP from a clean Git commit."""
    try:
        destination = package_source(_root(path), output)
    except (FileExistsError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Source package created:[/green] {destination}")


@app.command("verify-source-package")
def verify_source_package_command(
    archive: Annotated[Path, typer.Option("--archive")],
) -> None:
    """Verify source paths, file hashes, clean provenance, and package digest."""
    try:
        entries = verify_source_package(archive)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]Source package verified:[/green] {len(entries)} files")


@app.command("status")
def status_command(
    issue: Annotated[int | None, typer.Option("--issue", help="GitHub Issue number")] = None,
    path: Annotated[Path, typer.Option("--path", help="Project directory")] = Path("."),
) -> None:
    """Show one task or all local tasks."""
    store = _store(_root(path))
    try:
        tasks = [store.get_task_by_issue(issue)] if issue else store.list_tasks()
    except TaskNotFoundError as exc:
        console.print("[red]Task not found.[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table(tasks))


def _control(action: str, issue: int, path: Path) -> None:
    try:
        task = _store(_root(path)).request_control(issue, action)
    except (TaskNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table([task]))


@app.command("pause")
def pause_command(
    issue: Annotated[int, typer.Option("--issue")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Request a pause at the next safe stage boundary."""
    _control("pause", issue, path)


@app.command("resume")
def resume_command(
    issue: Annotated[int, typer.Option("--issue")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Restore the next state of a paused task."""
    _control("resume", issue, path)


@app.command("cancel")
def cancel_command(
    issue: Annotated[int, typer.Option("--issue")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Request cancellation at the next safe stage boundary."""
    _control("cancel", issue, path)


def _decision(
    approved: bool,
    issue: int,
    stage: str,
    commit_sha: str,
    approver: str,
    reason: str,
    path: Path,
) -> None:
    root = _root(path)
    loaded = _load(root)
    store = _store(root)
    try:
        existing = store.get_task_by_issue(issue)
        gateway = _gateway(root, loaded.project.github.gateway)
        _seed_mock_gateway(gateway, issue_number=issue, task=existing)
        task = record_decision(
            store,
            issue_number=issue,
            stage=stage,
            commit_sha=commit_sha,
            approver=approver,
            approved=approved,
            reason=reason,
            pull_request_number=existing.pull_request_number,
            gateway=gateway,
        )
    except (TaskNotFoundError, ValueError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(_task_table([task]))
    console.print("This command did not merge main or perform any production operation.")


@app.command("approve")
def approve_command(
    issue: Annotated[int, typer.Option("--issue")],
    stage: Annotated[str, typer.Option("--stage")],
    commit_sha: Annotated[str, typer.Option("--commit-sha")],
    approver: Annotated[str, typer.Option("--approver")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Record explicit approval for one issue, stage, and commit."""
    _decision(True, issue, stage, commit_sha, approver, reason, path)


@app.command("reject")
def reject_command(
    issue: Annotated[int, typer.Option("--issue")],
    stage: Annotated[str, typer.Option("--stage")],
    commit_sha: Annotated[str, typer.Option("--commit-sha")],
    approver: Annotated[str, typer.Option("--approver")],
    reason: Annotated[str, typer.Option("--reason")] = "",
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Record explicit rejection and return a human-gated task to rework."""
    _decision(False, issue, stage, commit_sha, approver, reason, path)


@app.command("logs")
def logs_command(
    issue: Annotated[int, typer.Option("--issue")],
    path: Annotated[Path, typer.Option("--path")] = Path("."),
) -> None:
    """Display sanitized audit events for a task."""
    store = _store(_root(path))
    try:
        task = store.get_task_by_issue(issue)
    except TaskNotFoundError as exc:
        console.print("[red]Task not found.[/red]")
        raise typer.Exit(1) from exc
    console.print_json(json.dumps(store.list_events(task.task_id), ensure_ascii=False, default=str))


if __name__ == "__main__":
    app()
