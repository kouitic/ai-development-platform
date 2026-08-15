import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
PINNED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "astral-sh/setup-uv": "c771a70e6277c0a99b617c7a806ffedaca235ff9",
    "gitleaks/gitleaks-action": "e0c47f4f8be36e29cdc102c57e68cb5cbf0e8d1e",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
}
USE_PATTERN = re.compile(r"uses:\s*([^@\s]+)@([0-9a-f]{40})(?:\s+#.*)?$")


def test_claude_sdk_is_optional_and_python_range_is_explicit() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    assert project["requires-python"] == ">=3.12,<3.14"
    assert not any(value.startswith("claude-agent-sdk") for value in project["dependencies"])
    assert project["optional-dependencies"]["claude"] == ["claude-agent-sdk>=0.2.134,<0.3"]
    assert "**/*.zip" in configuration["tool"]["hatch"]["build"]["exclude"]


def test_pytest_temp_variants_are_excluded_from_source_and_lint() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "/.pytest-tmp*/**" in configuration["tool"]["hatch"]["build"]["exclude"]
    assert ".pytest-tmp*" in configuration["tool"]["ruff"]["exclude"]
    for project_root in [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]:
        gitignore = (project_root / ".gitignore").read_text(encoding="utf-8")
        assert ".pytest-tmp*/" in gitignore


def test_lock_file_preserves_optional_claude_dependency() -> None:
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.12, <3.14"' in lock
    project_block = lock.split('name = "ai-development-platform"', maxsplit=1)[1].split(
        "[[package]]", maxsplit=1
    )[0]
    default_dependencies = project_block.split("[package.optional-dependencies]", maxsplit=1)[0]
    assert "claude-agent-sdk" not in default_dependencies
    assert "claude = [" in project_block
    assert '{ name = "claude-agent-sdk" }' in project_block
    assert re.search(
        r'\[\[package\]\]\r?\nname = "claude-agent-sdk"\r?\nversion = "0\.2\.134"',
        lock,
    )


def test_ci_runs_clean_install_contract_on_python_312_and_313() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'python-version: ["3.12", "3.13"]' in workflow
    assert "uv sync --frozen --extra dev\n" in workflow
    assert "find_spec('claude_agent_sdk') is None" in workflow
    assert "uv run --no-sync pytest" in workflow
    assert "uv run --no-sync ruff format --check ." in workflow
    assert "uv run --no-sync ruff check ." in workflow
    assert "uv run --no-sync mypy" in workflow
    assert "uv run --no-sync ai-dev package-source" in workflow
    assert "uv run --no-sync ai-dev verify-source-package" in workflow
    assert "uv sync --frozen --extra dev --extra claude" in workflow
    assert 'python -c "import claude_agent_sdk"' in workflow


def test_host_verification_requires_format_validate_and_ci_matrix_evidence() -> None:
    policy_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for policy_root in policy_roots:
        policy = (policy_root / ".ai-dev" / "policies" / "verification.yaml").read_text(
            encoding="utf-8"
        )
        assert "argv: [uv, run, ruff, format, --check, .]" in policy
        assert "argv: [uv, run, ai-dev, validate]" in policy
        assert 'required_ci_checks: ["quality (3.12)", "quality (3.13)"]' in policy
        assert "ci_wait_timeout_seconds: 900" in policy


def test_gitleaks_actions_receive_the_automatic_github_token() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for workflow_root in workflow_roots:
        ci = (workflow_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quality = (workflow_root / ".github" / "workflows" / "ai-quality-gates.yml").read_text(
            encoding="utf-8"
        )
        gitignore = (workflow_root / ".gitignore").read_text(encoding="utf-8")
        assert "pull-requests: read" in ci
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in ci
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in quality
        assert "results.sarif" in gitignore


def test_quality_workflows_install_claude_sandbox_dependencies() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for workflow_root in workflow_roots:
        quality = (workflow_root / ".github" / "workflows" / "ai-quality-gates.yml").read_text(
            encoding="utf-8"
        )
        assert "sudo apt-get update" in quality
        assert "sudo apt-get install --yes --no-install-recommends bubblewrap socat" in quality
        assert quality.index("bubblewrap socat") < quality.index(
            "uv sync --frozen --extra dev --extra claude"
        )


def test_quality_workflows_run_sanitized_provider_preflight_before_quality_gates() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for workflow_root in workflow_roots:
        quality = (workflow_root / ".github" / "workflows" / "ai-quality-gates.yml").read_text(
            encoding="utf-8"
        )
        assert "uv run ai-dev provider-preflight" in quality
        assert "provider-preflight.json" in quality
        assert (
            "AI_DEV_PROVIDER: ${{ secrets.ANTHROPIC_API_KEY != '' && 'claude' || 'mock' }}"
            in quality
        )
        assert quality.index("Secret history scan") < quality.index("provider-preflight")
        assert quality.index("provider-preflight") < quality.index("ai-dev quality-gates")


def test_manual_claude_workflow_uses_approved_issue_preflight_without_self_attestation() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    forbidden = [
        "AI_DEV_TRUSTED_EVENT",
        "AI_DEV_PRIVATE_REPOSITORY",
        "--tests-passed",
        "--static-analysis-passed",
    ]
    for workflow_root in workflow_roots:
        workflow = (workflow_root / ".github" / "workflows" / "ai-orchestrator.yml").read_text(
            encoding="utf-8"
        )
        assert "workflow_dispatch:" in workflow
        assert "ai-dev issue-preflight" in workflow
        assert "AI_DEV_PROVIDER: claude" in workflow
        assert "AI_DEV_GITHUB_GATEWAY: gh" in workflow
        assert "ai-dev run --issue" in workflow
        assert all(value not in workflow for value in forbidden)


def test_platform_ci_runs_on_main_while_generated_projects_protect_main() -> None:
    platform_ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    template_ci = (
        ROOT
        / "src"
        / "ai_dev_platform"
        / "templates"
        / "project"
        / ".github"
        / "workflows"
        / "ci.yml"
    ).read_text(encoding="utf-8")
    assert "branches: [main]" in platform_ci
    assert "branches-ignore: [main]" in template_ci


def test_all_external_actions_are_node24_reviewed_full_sha_pins() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    observed_by_root: list[list[tuple[str, str]]] = []
    for workflow_root in workflow_roots:
        observed: list[tuple[str, str]] = []
        for workflow in sorted((workflow_root / ".github" / "workflows").glob("*.yml")):
            for line in workflow.read_text(encoding="utf-8").splitlines():
                if "uses:" not in line:
                    continue
                match = USE_PATTERN.search(line.strip())
                assert match is not None, f"external Action is not pinned by full SHA: {line}"
                action, sha = match.groups()
                assert PINNED_ACTIONS.get(action) == sha
                observed.append((action, sha))
        assert set(action for action, _ in observed) == set(PINNED_ACTIONS)
        observed_by_root.append(observed)
    assert observed_by_root[0] == observed_by_root[1]

    pinning_record = (ROOT / "docs" / "github-actions-pinning.md").read_text(encoding="utf-8")
    for action, sha in PINNED_ACTIONS.items():
        assert action in pinning_record
        assert sha in pinning_record


def test_setup_uv_v9_preserves_the_previous_cache_pruning_policy() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for workflow_root in workflow_roots:
        ci = (workflow_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "enable-cache: true\n          prune-cache: true" in ci
        assert ci.index("actions/setup-python@") < ci.index("astral-sh/setup-uv@")
