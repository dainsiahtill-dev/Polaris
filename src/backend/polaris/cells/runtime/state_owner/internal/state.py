from __future__ import annotations

import asyncio
import logging
import os
import secrets
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import subprocess

    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ProcessHandle:
    process: subprocess.Popen | None = None
    log_handle: Any | None = None
    log_path: str = ""
    mode: str = ""
    started_at: float | None = None
    execution_id: str | None = None


@dataclass
class AppState:
    settings: Settings
    pm: ProcessHandle = field(default_factory=ProcessHandle)
    director: ProcessHandle = field(default_factory=ProcessHandle)
    last_pm_payload: dict[str, Any] | None = None
    # Per-process locks prevent concurrent start/stop requests from racing.
    pm_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    director_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Auth:
    """Authentication handler with strict security policy.

    Security rules:
    - Missing token = authentication disabled (for local/dev only)
    - Token configured = strict Bearer token validation required
    """

    def __init__(self, token: str) -> None:
        self.token = token or ""
        strict_when_empty = str(os.environ.get("KERNELONE_STRICT_AUTH_WHEN_EMPTY_TOKEN", "")).strip().lower()
        self._strict_when_empty_token = strict_when_empty in {"1", "true", "yes", "on"}

    def check(self, header_value: str) -> bool:
        """Validate authentication header against configured token.

        Returns True only if:
        - Valid Bearer token is provided matching configured token

        Returns False if:
        - No token is configured (local/dev mode requires token)
        - No authorization header provided
        - Invalid Bearer token format
        """
        if not self.token:
            # Security fix: empty token no longer bypasses auth.
            # Auth is always enforced; only explicit KERNELONE_STRICT_AUTH_WHEN_EMPTY_TOKEN=1
            # may disable it in dev/test environments (strict mode = rejection).
            return False
        if not header_value:
            return False
        if not header_value.lower().startswith("bearer "):
            return False
        value = header_value.split(" ", 1)[1].strip()
        return secrets.compare_digest(value, self.token)


class ConnectionState:
    def __init__(self) -> None:
        self.channels: set[str] = set()
        self.tail_state: dict[str, dict[str, Any]] = {}
        self.last_sizes: dict[str, int] = {}
        self.want_status: bool = False
