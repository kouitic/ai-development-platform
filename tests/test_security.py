import zipfile
from pathlib import Path

import pytest

from ai_dev_platform.domain.models import TaskRecord
from ai_dev_platform.infrastructure.state_store import SQLiteStateStore
from ai_dev_platform.security.paths import (
    assert_read_allowed,
    assert_write_allowed,
    normalize_relative,
)
from ai_dev_platform.security.runtime import CommandPolicy, NetworkPolicy, PolicyViolation
from ai_dev_platform.security.scanner import (
    SensitiveContentError,
    ensure_safe_to_persist,
    redact,
    scan_files,
    scan_text,
    scan_tree,
)


@pytest.mark.parametrize(
    ("value", "category"),
    [
        ("sk-" + "ant-" + "abcdefghijklmnopqrstuvwxyz1234", "anthropic_api_key"),
        ("ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456", "github_token"),
        ("AKIA" + "ABCDEFGHIJKLMNOP", "aws_access_key"),
        ("-----BEGIN " + "PRIVATE KEY-----", "private_key"),
        ("password:" + " this-is-a-real-password", "password_value"),
        (
            "postgresql://admin:" + "secretpass@database.invalid/app",
            "database_url_with_credentials",
        ),
    ],
)
def test_secret_patterns_are_detected_without_storing_value(value: str, category: str) -> None:
    findings = scan_text(value)
    assert any(finding.category == category for finding in findings)
    assert all(not hasattr(finding, "value") for finding in findings)
    assert value not in redact(value)
    with pytest.raises(SensitiveContentError):
        ensure_safe_to_persist(value)


def test_safe_dummy_value_is_allowed() -> None:
    assert scan_text("ANTHROPIC_API_KEY=\npassword: change_me") == []
    ensure_safe_to_persist("public dummy data")


def test_store_rejects_secret_in_state(tmp_path: Path) -> None:
    store = SQLiteStateStore(tmp_path / "state.sqlite3")
    task = TaskRecord(
        task_id="issue-1",
        issue_number=1,
        commit_sha="abcdef123456",
        context={"token": "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"},
    )
    with pytest.raises(SensitiveContentError):
        store.create_task(task)


def test_path_guard_rejects_env_credentials_and_escape(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    with pytest.raises(PermissionError):
        assert_read_allowed(root, root / ".env")
    with pytest.raises(PermissionError):
        normalize_relative(root, tmp_path / "outside.txt")
    allowed = root / "README.md"
    allowed.write_text("safe", encoding="utf-8")
    assert_read_allowed(root, allowed)


def test_symlink_cannot_bypass_protected_write_policy(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    protected = root / "docs" / "quality"
    allowed = root / "src"
    protected.mkdir(parents=True)
    allowed.mkdir()
    target = protected / "criteria.md"
    target.write_text("human owned", encoding="utf-8")
    link = allowed / "quality-link"
    try:
        link.symlink_to(protected, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {type(exc).__name__}")
    with pytest.raises(PermissionError, match="protected path"):
        assert_write_allowed(
            root,
            link / "criteria.md",
            ["src/**", "docs/quality/**"],
            ["docs/quality/**"],
        )


def test_resolved_symlink_target_is_checked_against_protected_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    requested = root / "src" / "quality-link" / "criteria.md"
    resolved_target = root / "docs" / "quality" / "criteria.md"
    original_resolve = Path.resolve

    def simulated_resolve(path: Path, strict: bool = False) -> Path:
        if path == requested:
            return original_resolve(resolved_target, strict=strict)
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", simulated_resolve)
    with pytest.raises(PermissionError, match="protected path"):
        assert_write_allowed(
            root,
            requested,
            ["src/**", "docs/quality/**"],
            ["docs/quality/**"],
        )


@pytest.mark.parametrize(
    "args",
    [
        ["git", "diff", "$(env)"],
        ["git", "push", "origin", "main"],
        ["git", "push", "--force", "origin", "ai/issue-1-x"],
        ["git", "merge", "main"],
        ["git", "reset", "--hard"],
        ["git", "clean", "-fd"],
        ["gh", "pr", "merge", "1"],
        ["env"],
        ["printenv"],
        ["aws", "s3", "sync"],
    ],
)
def test_command_policy_rejects_injection_secret_enumeration_and_dangerous_git(
    args: list[str],
) -> None:
    with pytest.raises(PolicyViolation):
        CommandPolicy().validate(args)


def test_network_policy_enforces_allowlist_and_read_only_behavior() -> None:
    policy = NetworkPolicy(mode="allowlist", allowed_domains=frozenset({"docs.example.com"}))
    policy.authorize("https://docs.example.com/reference")
    with pytest.raises(PolicyViolation, match="allowlisted"):
        policy.authorize("https://untrusted.example/reference")
    with pytest.raises(PolicyViolation, match="submission"):
        policy.authorize("https://docs.example.com/form", method="POST")
    with pytest.raises(PolicyViolation, match="authentication"):
        policy.authorize("https://user:password@docs.example.com/reference")
    with pytest.raises(PolicyViolation, match="sensitive"):
        policy.authorize(
            "https://docs.example.com/reference",
            data_classification="RESTRICTED_PRODUCTION_LIKE",
        )
    with pytest.raises(PolicyViolation, match="disabled"):
        NetworkPolicy(mode="disabled").authorize("https://docs.example.com")


def test_tree_scan_skips_generated_directories_and_archives(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    secret = "ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    excluded = [
        ".git/history.txt",
        ".venv/package.txt",
        ".uv-cache-run/cache.txt",
        ".uv-tools-review/tool.txt",
        ".uv-bin-review/tool.txt",
        ".pip-audit-cache-review/cache.txt",
        ".pytest-tmp/state.txt",
        ".pytest_cache/state.txt",
        ".mypy_cache/state.txt",
        ".ruff_cache/state.txt",
        "__pycache__/module.txt",
        "dist/output.txt",
        "build/output.txt",
        ".ai-dev/local/state.txt",
    ]
    for relative in excluded:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
    archive = root / "unknown.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("payload.txt", secret)

    assert scan_tree(root) == []


def test_explicit_git_candidate_scan_is_not_weakened_by_tree_exclusions(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    tracked = root / "dist" / "tracked.py"
    tracked.parent.mkdir(parents=True)
    tracked.write_text("token = 'ghp_" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456'", encoding="utf-8")

    assert scan_tree(root) == []
    findings = scan_files(root, ["dist/tracked.py"])
    assert [finding.category for finding in findings] == ["github_token"]


def test_explicit_zip_candidate_is_not_expanded(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    archive = root / "untrusted.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("payload.txt", "sk-ant-" + "abcdefghijklmnopqrstuvwxyz1234")
    assert scan_files(root, ["untrusted.zip"]) == []
