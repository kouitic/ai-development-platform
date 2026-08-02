import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_claude_sdk_is_optional_and_python_range_is_explicit() -> None:
    configuration = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = configuration["project"]
    assert project["requires-python"] == ">=3.12,<3.14"
    assert not any(value.startswith("claude-agent-sdk") for value in project["dependencies"])
    assert project["optional-dependencies"]["claude"] == ["claude-agent-sdk>=0.1,<0.2"]
    assert "**/*.zip" in configuration["tool"]["hatch"]["build"]["exclude"]


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


def test_gitleaks_actions_receive_the_automatic_github_token() -> None:
    workflow_roots = [ROOT, ROOT / "src" / "ai_dev_platform" / "templates" / "project"]
    for workflow_root in workflow_roots:
        ci = (workflow_root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        quality = (workflow_root / ".github" / "workflows" / "ai-quality-gates.yml").read_text(
            encoding="utf-8"
        )
        assert "pull-requests: read" in ci
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in ci
        assert "GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}" in quality
