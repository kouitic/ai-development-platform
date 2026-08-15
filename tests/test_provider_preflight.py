import json
from pathlib import Path
from typing import Literal

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
            ProviderPreflightStageResult(stage="models_api", status="PASS"),
            ProviderPreflightStageResult(
                stage="token_count_api",
                status="WARN",
                error_code="provider_api_error_400_workspace_restriction",
            ),
            ProviderPreflightStageResult(
                stage="messages_api",
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
    assert persisted["stages"][-1]["stage"] == "messages_api"
    assert set(persisted["stages"][-1]) == {"stage", "status", "error_code"}
    assert destination.with_suffix(".json.sha256").read_text(encoding="ascii").strip() == (
        artifact_digest(payload)
    )


def test_provider_preflight_report_accepts_narrow_nonblocking_warning() -> None:
    report = ProviderPreflightReport(
        provider="claude",
        commit_sha="a" * 40,
        overall_status="PASS_WITH_WARNINGS",
        stages=[
            ProviderPreflightStageResult(stage="models_api", status="PASS"),
            ProviderPreflightStageResult(
                stage="token_count_api",
                status="WARN",
                error_code="provider_api_error_400_workspace_restriction",
            ),
            ProviderPreflightStageResult(stage="messages_api", status="PASS"),
            ProviderPreflightStageResult(stage="agent_sdk", status="PASS"),
        ],
    )

    assert report.overall_status == "PASS_WITH_WARNINGS"


@pytest.mark.parametrize(
    ("stage", "error_code"),
    [
        ("messages_api", "provider_api_error_400_workspace_restriction"),
        ("token_count_api", "provider_api_error_400_invalid_request"),
    ],
)
def test_provider_preflight_warning_policy_cannot_be_widened(
    stage: Literal["messages_api", "token_count_api"],
    error_code: str,
) -> None:
    with pytest.raises(ValueError, match="only the Token Counting workspace restriction"):
        ProviderPreflightStageResult(
            stage=stage,
            status="WARN",
            error_code=error_code,
        )


def test_provider_preflight_report_cannot_escape_local_artifacts(tmp_path: Path) -> None:
    report = ProviderPreflightReport(
        provider="mock",
        commit_sha="mock000000000000000000000000000000000000",
        overall_status="SKIPPED",
    )

    with pytest.raises(ValueError, match=r"under \.ai-dev/local"):
        write_provider_preflight_report(tmp_path, Path("diagnostic.json"), report)
