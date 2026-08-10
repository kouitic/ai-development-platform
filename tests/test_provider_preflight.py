import json
from pathlib import Path

import pytest

from ai_dev_platform.application.provider_preflight import write_provider_preflight_report
from ai_dev_platform.application.quality_artifacts import artifact_digest
from ai_dev_platform.domain.models import (
    ProviderPreflightReport,
    ProviderPreflightStageResult,
)


def test_provider_preflight_report_is_digest_protected_and_sanitized(tmp_path: Path) -> None:
    report = ProviderPreflightReport(
        provider="claude",
        commit_sha="a" * 40,
        overall_status="ERROR",
        stages=[
            ProviderPreflightStageResult(stage="basic", status="PASS"),
            ProviderPreflightStageResult(
                stage="structured_output",
                status="ERROR",
                error_code="provider_api_error_400_invalid_request",
            ),
        ],
    )

    destination = write_provider_preflight_report(
        tmp_path,
        Path(".ai-dev/local/quality-artifacts/provider-preflight.json"),
        report,
    )

    payload = destination.read_bytes()
    persisted = json.loads(payload)
    assert persisted["overall_status"] == "ERROR"
    assert persisted["stages"][-1]["stage"] == "structured_output"
    assert set(persisted["stages"][-1]) == {"stage", "status", "error_code"}
    assert destination.with_suffix(".json.sha256").read_text(encoding="ascii").strip() == (
        artifact_digest(payload)
    )


def test_provider_preflight_report_cannot_escape_local_artifacts(tmp_path: Path) -> None:
    report = ProviderPreflightReport(
        provider="mock",
        commit_sha="mock000000000000000000000000000000000000",
        overall_status="SKIPPED",
    )

    with pytest.raises(ValueError, match=r"under \.ai-dev/local"):
        write_provider_preflight_report(tmp_path, Path("diagnostic.json"), report)
