"""Collect and protect successful GitHub CI evidence for formal reviews."""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Callable
from pathlib import Path
from time import monotonic, sleep

from ai_dev_platform.domain.models import CiEvidenceReport, GitHubCheckRunEvidence
from ai_dev_platform.infrastructure.github import GitHubGateway
from ai_dev_platform.security.scanner import ensure_safe_to_persist


def validate_ci_evidence_report(report: CiEvidenceReport) -> None:
    """Require one successful, completed Check Run per configured name and exact SHA."""
    required = report.required_check_names
    if len(required) != len(set(required)):
        raise ValueError("required CI check names must be unique")
    checks_by_name = {check.name: check for check in report.checks}
    if len(checks_by_name) != len(report.checks) or set(checks_by_name) != set(required):
        raise ValueError("CI evidence does not exactly match the required checks")
    if any(
        check.commit_sha != report.commit_sha
        or check.status != "completed"
        or check.conclusion != "success"
        or check.details_url is None
        or check.completed_at is None
        for check in report.checks
    ):
        raise ValueError("CI evidence is not a completed success for the exact commit")


def collect_required_ci_evidence(
    github: GitHubGateway,
    commit_sha: str,
    required_check_names: list[str],
    *,
    timeout_seconds: int,
    poll_interval_seconds: float = 10.0,
    clock: Callable[[], float] = monotonic,
    sleeper: Callable[[float], None] = sleep,
) -> CiEvidenceReport:
    """Wait for the latest required Check Runs and return only successful metadata."""
    if len(required_check_names) != len(set(required_check_names)):
        raise ValueError("required CI check names must be unique")
    deadline = clock() + timeout_seconds
    while True:
        latest: dict[str, GitHubCheckRunEvidence] = {}
        for check in github.get_commit_check_runs(commit_sha):
            if check.commit_sha != commit_sha:
                raise ValueError("GitHub Check Run belongs to another commit")
            current = latest.get(check.name)
            if current is None or check.check_run_id > current.check_run_id:
                latest[check.name] = check

        selected = [latest[name] for name in required_check_names if name in latest]
        if any(check.status == "completed" and check.conclusion != "success" for check in selected):
            raise ValueError("a required CI check completed without success")
        if len(selected) == len(required_check_names) and all(
            check.status == "completed"
            and check.conclusion == "success"
            and check.completed_at is not None
            for check in selected
        ):
            report = CiEvidenceReport(
                commit_sha=commit_sha,
                required_check_names=list(required_check_names),
                checks=selected,
            )
            validate_ci_evidence_report(report)
            return report
        remaining = deadline - clock()
        if remaining <= 0:
            raise ValueError("required CI evidence was not available before timeout")
        sleeper(min(poll_interval_seconds, remaining))


def write_ci_evidence_report(path: Path, report: CiEvidenceReport) -> Path:
    """Write canonical CI evidence JSON and an adjacent SHA-256 digest."""
    validate_ci_evidence_report(report)
    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    ensure_safe_to_persist(payload)
    destination.write_text(payload, encoding="utf-8")
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    destination.with_name(f"{destination.name}.sha256").write_text(digest, encoding="utf-8")
    return destination


def read_ci_evidence_report(path: Path, *, expected_commit_sha: str) -> CiEvidenceReport:
    """Verify the adjacent digest, schema, successful checks, and expected commit SHA."""
    source = path.resolve()
    digest_path = source.with_name(f"{source.name}.sha256")
    try:
        payload = source.read_text(encoding="utf-8")
        expected_digest = digest_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise ValueError("CI evidence artifact is missing") from exc
    actual_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_digest):
        raise ValueError("CI evidence artifact digest mismatch")
    try:
        raw = json.loads(payload)
        report = CiEvidenceReport.model_validate(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("CI evidence artifact is invalid") from exc
    validate_ci_evidence_report(report)
    if report.commit_sha != expected_commit_sha:
        raise ValueError("CI evidence artifact belongs to another commit")
    return report
