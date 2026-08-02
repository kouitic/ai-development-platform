"""Runtime command and network policy enforcement."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from ai_dev_platform.security.scanner import SensitiveContentError, ensure_safe_to_persist


class PolicyViolation(PermissionError):
    """An operation violated an enforced runtime policy."""


_SHELL_META = re.compile(r"(?:\||&&|;|>|<|\$\(|`|\r|\n)")


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    """Allow only reviewed argv prefixes and deny dangerous operations explicitly."""

    allowed_prefixes: tuple[tuple[str, ...], ...] = (
        ("python",),
        ("pytest",),
        ("uv", "run", "pytest"),
        ("uv", "run", "ruff"),
        ("uv", "run", "mypy"),
        ("git", "status"),
        ("git", "diff"),
        ("git", "rev-parse"),
    )

    def validate(self, args: list[str]) -> tuple[str, ...]:
        """Return immutable argv after rejecting injection and forbidden commands."""
        if not args or any(_SHELL_META.search(argument) for argument in args):
            raise PolicyViolation("shell syntax and empty commands are forbidden")
        normalized = tuple(args)
        executable = Path(args[0]).name.lower()
        lowered = tuple(argument.lower() for argument in args)
        if executable in {"env", "printenv", "set"}:
            raise PolicyViolation("environment enumeration is forbidden")
        if executable in {"aws", "aws.exe"}:
            raise PolicyViolation("AWS operations are outside this runtime policy")
        if lowered[:3] == ("gh", "pr", "merge"):
            raise PolicyViolation("pull request merge is forbidden")
        if lowered[:2] == ("git", "merge"):
            raise PolicyViolation("git merge is forbidden")
        if lowered[:2] == ("git", "reset") or lowered[:2] == ("git", "clean"):
            raise PolicyViolation("destructive git commands are forbidden")
        if lowered[:2] == ("git", "push"):
            if any(value in {"--force", "-f", "--force-with-lease"} for value in lowered):
                raise PolicyViolation("force push is forbidden")
            if "main" in lowered[2:]:
                raise PolicyViolation("pushing main is forbidden")
        if not any(normalized[: len(prefix)] == prefix for prefix in self.allowed_prefixes):
            raise PolicyViolation("command is not on the reviewed allowlist")
        return normalized

    def run(self, args: list[str], *, root: Path, timeout_seconds: int = 300) -> str:
        """Execute validated argv without a shell and suppress command error output."""
        validated = self.validate(args)
        try:
            result = subprocess.run(
                list(validated),
                cwd=root.resolve(),
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_seconds,
                shell=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PolicyViolation("approved command execution failed") from exc
        if result.returncode != 0:
            raise PolicyViolation("approved command returned a failure")
        return result.stdout


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    """Authorize read-only external access without data submission capabilities."""

    mode: str = "disabled"
    allowed_domains: frozenset[str] = field(default_factory=frozenset)

    def authorize(
        self,
        url: str,
        *,
        method: str = "GET",
        action: str = "read",
        data_classification: str = "PUBLIC_DUMMY",
    ) -> None:
        """Reject disallowed hosts, writes, authentication, execution, and sensitive data."""
        if self.mode in {"none", "disabled"}:
            raise PolicyViolation("network access is disabled")
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            raise PolicyViolation("only HTTP(S) URLs are supported")
        if parsed.username is not None or parsed.password is not None:
            raise PolicyViolation("network authentication is forbidden")
        try:
            ensure_safe_to_persist(url)
        except SensitiveContentError as exc:
            raise PolicyViolation("sensitive URL content is forbidden") from exc
        if method.upper() not in {"GET", "HEAD"} or action != "read":
            raise PolicyViolation("external submission and non-read actions are forbidden")
        if data_classification not in {"PUBLIC_DUMMY", "SYNTHETIC"}:
            raise PolicyViolation("sensitive or production-like data cannot be transmitted")
        if self.mode == "allowlist" and not any(
            host == domain or host.endswith(f".{domain}") for domain in self.allowed_domains
        ):
            raise PolicyViolation("network destination is not allowlisted")
        if self.mode not in {"allowlist", "unrestricted_read"}:
            raise PolicyViolation("unsupported network policy mode")
