"""Write digest-protected, secret-free provider preflight evidence."""

from __future__ import annotations

import json
from pathlib import Path

from ai_dev_platform.application.quality_artifacts import artifact_digest
from ai_dev_platform.domain.models import ProviderPreflightReport
from ai_dev_platform.security.scanner import ensure_safe_to_persist


def write_provider_preflight_report(
    root: Path,
    path: Path,
    report: ProviderPreflightReport,
) -> Path:
    """Persist a canonical report only in the repository-local artifact directory."""
    root = root.resolve()
    destination = (path if path.is_absolute() else root / path).resolve()
    try:
        relative = destination.relative_to(root)
    except ValueError as exc:
        raise ValueError("provider preflight artifact must stay inside the repository") from exc
    if relative.parts[:2] != (".ai-dev", "local"):
        raise ValueError("provider preflight artifact must be under .ai-dev/local")

    payload = json.dumps(
        report.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    ensure_safe_to_persist(payload.decode("utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    destination.with_suffix(f"{destination.suffix}.sha256").write_text(
        artifact_digest(payload), encoding="ascii", newline="\n"
    )
    return destination
