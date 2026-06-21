from __future__ import annotations

import asyncio
import logging
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


_DEV_FALLBACK_TOKEN = "polaris-local-dev"


class Auth:
    """Authentication handler with strict security policy.

    Security rules:
    - Missing token = all connections rejected
    - Token configured = strict Bearer token validation required
    - In development mode (no explicit token configured before startup),
      the well-known dev token is accepted as a fallback so that the
      Vite dev-server frontend can reach the backend without IPC.
    """

    def __init__(self, token: str, *, allow_dev_fallback: bool = False) -> None:
        self.token = token or ""
        self._allow_dev_fallback = allow_dev_fallback and bool(self.token)

    def check(self, header_value: str) -> bool:
        """Validate authentication header against configured token.

        Returns True only if:
        - Valid Bearer token is provided matching configured token
        - OR dev fallback is active and the dev token is provided

        Returns False if:
        - No token is configured
        - No authorization header provided
        - Invalid Bearer token format
        """
        if not self.token:
            return False
        if not header_value:
            return False
        if not header_value.lower().startswith("bearer "):
            return False
        value = header_value.split(" ", 1)[1].strip()
        if secrets.compare_digest(value, self.token):
            return True
        # Dev fallback: accept the well-known dev token when the backend
        # was started with an auto-generated token (i.e. no explicit token
        # was configured via env/config).  This bridges the gap between
        # the backend's random token and the Vite frontend's hardcoded
        # DEFAULT_BACKEND_TOKEN when running outside Electron.
        return bool(self._allow_dev_fallback and secrets.compare_digest(value, _DEV_FALLBACK_TOKEN))


class ConnectionState:
    def __init__(self) -> None:
        self.channels: set[str] = set()
        self.tail_state: dict[str, dict[str, Any]] = {}
        self.last_sizes: dict[str, int] = {}
        self.want_status: bool = False
        self.active_connections: int = 0
        self.total_connections: int = 0
        self.last_connection_id: str = ""
        self.last_event: str = ""
        self.last_error: str = ""
        self.last_updated_at: float = 0.0
