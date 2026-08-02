"""Digest-protected, sanitized quality result transfer between ordered stages."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_dev_platform.domain.models import (
    BusinessReviewResult,
    FindingSeverity,
    FindingStatus,
    QaAssessmentResult,
    QualityArtifactEnvelope,
    ReviewType,
    StageResult,
    SystemReviewResult,
    TaskRecord,
)
from ai_dev_platform.security.scanner import ensure_safe_to_persist


class QualityArtifactError(ValueError):
    """A quality artifact failed its integrity or target checks."""


def artifact_digest(payload: bytes) -> str:
    """Return the lowercase SHA-256 digest used by artifact transfer."""
    return hashlib.sha256(payload).hexdigest()


def _result_for(task: TaskRecord, stage: ReviewType) -> StageResult:
    result: StageResult | None
    if stage == ReviewType.SYSTEM:
        result = task.evidence.system_reviews[-1] if task.evidence.system_reviews else None
    elif stage == ReviewType.BUSINESS:
        result = task.evidence.business_reviews[-1] if task.evidence.business_reviews else None
    else:
        result = task.evidence.qa_assessments[-1] if task.evidence.qa_assessments else None
    if result is None:
        raise QualityArtifactError("requested review result is missing")
    return result


def build_quality_artifact(task: TaskRecord, stage: ReviewType) -> QualityArtifactEnvelope:
    """Build an envelope containing no Issue body, diff, credentials, or raw command output."""
    if task.pull_request_number is None:
        raise QualityArtifactError("quality artifact requires a Pull Request")
    result = _result_for(task, stage)
    blocking = [
        finding.id
        for finding in task.evidence.unresolved_findings
        if finding.status not in {FindingStatus.RESOLVED, FindingStatus.ACCEPTED_BY_HUMAN}
        and (
            finding.blocking
            or finding.severity in {FindingSeverity.CRITICAL, FindingSeverity.MAJOR}
        )
    ]
    return QualityArtifactEnvelope(
        stage=stage,
        issue_number=task.issue_number,
        pull_request_number=task.pull_request_number,
        commit_sha=task.commit_sha,
        review_run_id=result.run_id,
        decision=result.decision,
        blocking_finding_ids=blocking,
        evidence_references=[reference.reference for reference in result.evidence],
        human_summary=result.summary,
        result=result.model_dump(mode="json"),
    )


def write_quality_artifact(path: Path, artifact: QualityArtifactEnvelope) -> Path:
    """Write one canonical JSON file and its adjacent SHA-256 digest."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        artifact.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    ensure_safe_to_persist(payload.decode("utf-8"))
    path.write_bytes(payload)
    path.with_suffix(f"{path.suffix}.sha256").write_text(
        artifact_digest(payload), encoding="ascii", newline="\n"
    )
    return path


def read_quality_artifact(
    path: Path,
    *,
    expected_stage: ReviewType,
    issue_number: int,
    pull_request_number: int,
    commit_sha: str,
) -> QualityArtifactEnvelope:
    """Verify digest, schema, stage, Issue, PR, and exact head SHA before consumption."""
    path = path.resolve()
    digest_path = path.with_suffix(f"{path.suffix}.sha256")
    try:
        payload = path.read_bytes()
        expected_digest = digest_path.read_text(encoding="ascii").strip()
    except OSError as exc:
        raise QualityArtifactError("quality artifact or digest is missing") from exc
    if not expected_digest or artifact_digest(payload) != expected_digest:
        raise QualityArtifactError("quality artifact digest mismatch")
    try:
        artifact = QualityArtifactEnvelope.model_validate_json(payload)
    except ValueError as exc:
        raise QualityArtifactError("quality artifact schema is invalid") from exc
    if artifact.stage != expected_stage:
        raise QualityArtifactError("quality artifact stage mismatch")
    if artifact.issue_number != issue_number:
        raise QualityArtifactError("quality artifact Issue mismatch")
    if artifact.pull_request_number != pull_request_number:
        raise QualityArtifactError("quality artifact Pull Request mismatch")
    if artifact.commit_sha != commit_sha:
        raise QualityArtifactError("quality artifact commit SHA mismatch")
    if expected_stage == ReviewType.SYSTEM:
        SystemReviewResult.model_validate(artifact.result)
    elif expected_stage == ReviewType.BUSINESS:
        BusinessReviewResult.model_validate(artifact.result)
    else:
        QaAssessmentResult.model_validate(artifact.result)
    return artifact
