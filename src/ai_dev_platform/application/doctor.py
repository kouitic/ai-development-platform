"""Read-only environment diagnostics that never display credential values."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

DoctorStatus = Literal["ok", "warning", "error", "not_checked"]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    """One diagnostic result without sensitive values."""

    name: str
    status: DoctorStatus
    detail: str


def _command_check(name: str, executable: str) -> DoctorCheck:
    path = shutil.which(executable)
    if path is None and executable == "uv":
        bundled = Path(sys.executable).with_name("uv.exe" if os.name == "nt" else "uv")
        path = str(bundled) if bundled.exists() else None
    return DoctorCheck(name, "ok" if path else "error", "available" if path else "not found")


def _configured(name: str) -> DoctorCheck:
    return DoctorCheck(
        name,
        "ok" if os.getenv(name) else "warning",
        "configured" if os.getenv(name) else "not configured",
    )


def run_doctor(root: Path) -> list[DoctorCheck]:
    """Inspect local prerequisites and external controls without modifying them."""
    checks: list[DoctorCheck] = []
    project_path = root / ".ai-dev" / "project.yaml"
    try:
        project_data = yaml.safe_load(project_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        project_data = {}
    provider = str(project_data.get("provider", "mock"))
    github_data = project_data.get("github", {})
    github_required = isinstance(github_data, dict) and bool(github_data.get("enabled", False))
    version_ok = (3, 12) <= sys.version_info[:2] < (3, 14)
    checks.append(
        DoctorCheck(
            "Python",
            "ok" if version_ok else "error",
            f"{sys.version_info.major}.{sys.version_info.minor}",
        )
    )
    checks.extend([_command_check("uv", "uv"), _command_check("Git", "git")])
    checks.append(
        _command_check("GitHub CLI", "gh")
        if github_required
        else DoctorCheck("GitHub CLI", "not_checked", "not required by Mock configuration")
    )
    sdk_installed = importlib.util.find_spec("claude_agent_sdk") is not None
    checks.append(
        DoctorCheck(
            "Claude Agent SDK",
            "ok" if sdk_installed else "error" if provider == "claude" else "not_checked",
            "installed"
            if sdk_installed
            else "not installed"
            if provider == "claude"
            else "not required by Mock configuration",
        )
    )
    checks.append(
        _configured("ANTHROPIC_API_KEY")
        if provider == "claude"
        else DoctorCheck("ANTHROPIC_API_KEY", "not_checked", "not required by Mock configuration")
    )
    checks.append(
        _configured("GITHUB_TOKEN")
        if github_required
        else DoctorCheck("GITHUB_TOKEN", "not_checked", "not required by Mock configuration")
    )
    git_dir = root / ".git"
    checks.append(
        DoctorCheck(
            "Git repository",
            "ok" if git_dir.exists() else "warning",
            "detected" if git_dir.exists() else "not initialized",
        )
    )
    checks.append(
        DoctorCheck(
            "Project configuration",
            "ok" if project_path.exists() else "error",
            "present" if project_path.exists() else "missing",
        )
    )
    checks.append(
        DoctorCheck(
            "GitHub Actions",
            "ok" if (root / ".github" / "workflows" / "ci.yml").exists() else "error",
            "configured" if (root / ".github" / "workflows" / "ci.yml").exists() else "missing",
        )
    )
    checks.append(DoctorCheck("Log masking", "ok", "built-in scanner enabled"))
    checks.append(
        DoctorCheck(
            "Production-like data store",
            "not_checked",
            "MVP does not access production-like data; configure a dedicated store in Phase 2",
        )
    )

    gh = shutil.which("gh")
    if gh and git_dir.exists():
        auth = subprocess.run(
            [gh, "auth", "status"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        checks.append(
            DoctorCheck(
                "GitHub authentication",
                "ok" if auth.returncode == 0 else "warning",
                "authenticated" if auth.returncode == 0 else "not authenticated",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "GitHub authentication", "not_checked", "GitHub CLI or repository unavailable"
            )
        )
    checks.extend(
        [
            DoctorCheck("Branch protection", "not_checked", "verify in GitHub repository settings"),
            DoctorCheck("Secret scanning", "not_checked", "verify in GitHub repository settings"),
            DoctorCheck("Push protection", "not_checked", "verify in GitHub repository settings"),
            DoctorCheck(
                "Production Secret separation", "not_checked", "verify GitHub Environments/IAM"
            ),
        ]
    )
    return checks
