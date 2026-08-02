import subprocess
from pathlib import Path

import pytest

from ai_dev_platform.application.conversation import (
    merge_confirmed_decisions,
    render_issue_body,
    structure_request,
)
from ai_dev_platform.application.doctor import run_doctor
from ai_dev_platform.infrastructure.github import (
    GhCliGateway,
    GitHubError,
    MockGitHubGateway,
    issue_branch_name,
)


def test_conversation_structures_business_facing_issue() -> None:
    draft = structure_request("請求書の承認漏れを通知したい")
    body = render_issue_body(draft)
    assert draft.business_requirements
    assert draft.human_decisions_required
    assert "## 受入条件" in body
    assert "## セキュリティへの影響" in body
    assert "本番反映には別途" in body


def test_confirmed_issue_section_is_replaced_without_overwriting_issue_body() -> None:
    first = merge_confirmed_decisions(
        "original requirements", "<!-- ai-dev-confirmed-start -->\nA\n<!-- ai-dev-confirmed-end -->"
    )
    second = merge_confirmed_decisions(
        first, "<!-- ai-dev-confirmed-start -->\nB\n<!-- ai-dev-confirmed-end -->"
    )
    assert second.startswith("original requirements")
    assert "\nB\n" in second
    assert "\nA\n" not in second


def test_mock_github_issue_branch_comment_and_error() -> None:
    gateway = MockGitHubGateway(next_issue_number=5)
    number = gateway.create_issue("Title", "Body", ["ai:ready"])
    branch = gateway.create_branch(number, "Add Invoice Notice")
    gateway.add_comment(number, "formal result")
    assert number == 5
    assert branch == "ai/issue-5-add-invoice-notice"
    assert gateway.comments == [(5, "formal result")]
    gateway.fail_with = "simulated API error"
    with pytest.raises(GitHubError):
        gateway.create_issue("x", "y", [])


def test_issue_branch_name_sanitizes_description() -> None:
    assert issue_branch_name(9, "日本語 only") == "ai/issue-9-only"
    assert issue_branch_name(10, "***") == "ai/issue-10-task"


def test_gh_gateway_suppresses_command_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["gh"], 1, stdout="", stderr="token=secret")

    monkeypatch.setattr(subprocess, "run", failed)
    gateway = GhCliGateway(tmp_path)
    with pytest.raises(GitHubError, match="sensitive details were suppressed"):
        gateway.add_comment(1, "body")


def test_gh_gateway_reads_japanese_json_as_utf8(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    options: dict[str, object] = {}

    def succeeded(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        options.update(kwargs)
        stdout = (
            '{"number":1,"title":"限定受入","body":"日本語の要件",'
            '"labels":[],"url":"https://example.invalid/issues/1"}'
        )
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(subprocess, "run", succeeded)
    issue = GhCliGateway(tmp_path).get_issue(1)

    assert issue.title == "限定受入"
    assert issue.body == "日本語の要件"
    assert options["encoding"] == "utf-8"
    assert options["errors"] == "strict"


def test_doctor_never_reports_environment_values(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-" + "ant-" + "abcdefghijklmnopqrstuvwxyz1234")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456")
    checks = run_doctor(initialized_project)
    rendered = "\n".join(f"{check.name}:{check.detail}" for check in checks)
    assert "configured" in rendered
    assert "sk-ant" not in rendered
    assert "ghp_" not in rendered


def test_doctor_does_not_require_claude_sdk_for_mock_configuration(
    initialized_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda _: None)
    checks = {check.name: check for check in run_doctor(initialized_project)}
    assert checks["Claude Agent SDK"].status == "not_checked"
    assert "not required" in checks["Claude Agent SDK"].detail
