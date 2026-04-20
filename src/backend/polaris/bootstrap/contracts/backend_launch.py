"""DTOs for backend server launch operations.

This module defines the request and result types for launching
the Polaris backend server in a type-safe, immutable manner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from polaris.domain.models.config_snapshot import ConfigSnapshot, ConfigValidationResult
except ImportError:
    # Fallback for standalone usage
    ConfigSnapshot = Any  # type: ignore[misc,assignment]
    ConfigValidationResult = Any  # type: ignore[misc,assignment]


@dataclass(frozen=True)
class BackendLaunchRequest:
    """Request to launch the backend server.

    All fields are immutable. Validation is performed at construction time.

    Attributes:
        host: Server bind host (None = use config defaults)
        port: Server port (0 = auto-assign, default: 0)
        cors_origins: List of allowed CORS origins (None = use config defaults)
        token: Authentication token (None = auto-generate)
        workspace: Workspace root path
        explicit_workspace: Whether workspace came from an explicit external override
        ramdisk_root: Optional ramdisk path for runtime files
        log_level: Logging level (None = use config defaults)
        debug_tracing: Enable debug tracing
        self_upgrade_mode: Allow Polaris meta-project to be targeted
        config_snapshot: Complete configuration snapshot
    """

    host: str | None = None
    port: int = 0  # 0 means auto-assign
    cors_origins: list[str] | None = None
    token: str | None = None
    workspace: Path = field(default_factory=Path.cwd)
    explicit_workspace: bool = False
    ramdisk_root: Path | None = None
    log_level: str | None = None
    debug_tracing: bool = False
    self_upgrade_mode: bool | None = None
    config_snapshot: Any | None = None

    def __post_init__(self) -> None:
        valid_levels = {"debug", "info", "warning", "error", "critical"}

        normalized_host = self.host.strip() if isinstance(self.host, str) else ""
        object.__setattr__(self, "host", normalized_host or None)

        if self.log_level is None:
            normalized_level: str | None = None
        else:
            candidate = str(self.log_level).strip().lower()
            normalized_level = candidate if candidate in valid_levels else "info"
        object.__setattr__(self, "log_level", normalized_level)

        # BUG-007 fix: dataclass does not enforce the Path type annotation, so
        # callers can pass workspace=None even though the field has a
        # default_factory.  Path(None) raises TypeError, so guard explicitly.
        raw_ws = self.workspace
        if raw_ws is None:
            normalized_workspace: Path = Path.cwd()
        elif isinstance(raw_ws, Path):
            normalized_workspace = raw_ws
        else:
            normalized_workspace = Path(raw_ws)
        object.__setattr__(self, "workspace", normalized_workspace)

        if self.ramdisk_root is not None and not isinstance(self.ramdisk_root, Path):
            object.__setattr__(self, "ramdisk_root", Path(self.ramdisk_root))

        if self.self_upgrade_mode is not None:
            object.__setattr__(self, "self_upgrade_mode", bool(self.self_upgrade_mode))

        if self.cors_origins:
            normalized_origins = [str(origin).strip() for origin in self.cors_origins if str(origin).strip()]
            object.__setattr__(self, "cors_origins", normalized_origins or None)
        else:
            object.__setattr__(self, "cors_origins", None)

    def validate(self) -> ConfigValidationResult:
        """Comprehensive validation of launch request.

        Returns:
            ConfigValidationResult with validation status and any errors
        """
        # Import here to avoid circular dependency
        try:
            from polaris.domain.models.config_snapshot import (
                ConfigValidationResult as ValidationResult,
            )
        except ImportError:
            # Simple fallback
            class SimpleValidationResult:  # type: ignore
                def __init__(self) -> None:
                    self.is_valid: bool = True
                    self.errors: list[str] = []
                    self.warnings: list[str] = []

                def add_error(self, msg: str) -> SimpleValidationResult:
                    self.is_valid = False
                    self.errors.append(msg)
                    return self

                def add_warning(self, msg: str) -> SimpleValidationResult:
                    self.warnings.append(msg)
                    return self

            ValidationResult = SimpleValidationResult  # type: ignore[misc,assignment]

        result = ValidationResult()

        # Check port range
        if self.port < 0 or self.port > 65535:
            result = result.add_error(f"Invalid port: {self.port}")

        # Check workspace permissions (Windows compatible)
        if not self.workspace.is_dir():
            result = result.add_error(f"Not a directory: {self.workspace}")
        else:
            # Check writable (best effort on Windows)
            try:
                test_file = self.workspace / ".write_test"
                test_file.write_text("")
                test_file.unlink()
            except (OSError, PermissionError):
                if hasattr(result, "add_warning"):
                    result = result.add_warning(f"Workspace may not be writable: {self.workspace}")

        # Validate ramdisk if specified
        if self.ramdisk_root:
            if not self.ramdisk_root.exists():
                result = result.add_error(f"Ramdisk root does not exist: {self.ramdisk_root}")
            elif not self.ramdisk_root.is_dir():
                result = result.add_error(f"Ramdisk root is not a directory: {self.ramdisk_root}")

        # Validate config snapshot if provided
        if self.config_snapshot and hasattr(self.config_snapshot, "validate"):
            snapshot_result = self.config_snapshot.validate()
            if hasattr(snapshot_result, "errors"):
                for error in snapshot_result.errors:
                    result = result.add_error(error)
            if hasattr(snapshot_result, "warnings"):
                for warning in snapshot_result.warnings:
                    result = result.add_warning(warning)

        return result

    def to_uvicorn_options(self) -> dict[str, Any]:
        """Convert to uvicorn run options.

        Returns:
            Dictionary suitable for passing to uvicorn.run()
        """
        return {
            "host": self.host or "127.0.0.1",
            "port": self.port,
            "log_level": (self.log_level or "info").lower(),
            "factory": True,  # We use app factory pattern
            "access_log": self.debug_tracing,
        }

    def with_port(self, port: int) -> BackendLaunchRequest:
        """Create new request with specified port.

        Args:
            port: Port number to use

        Returns:
            New BackendLaunchRequest with updated port
        """
        return BackendLaunchRequest(
            host=self.host,
            port=port,
            cors_origins=self.cors_origins,
            token=self.token,
            workspace=self.workspace,
            explicit_workspace=self.explicit_workspace,
            ramdisk_root=self.ramdisk_root,
            log_level=self.log_level,
            debug_tracing=self.debug_tracing,
            self_upgrade_mode=self.self_upgrade_mode,
            config_snapshot=self.config_snapshot,
        )

    def with_workspace(self, workspace: Path) -> BackendLaunchRequest:
        """Create new request with specified workspace.

        Args:
            workspace: New workspace path

        Returns:
            New BackendLaunchRequest with updated workspace
        """
        return BackendLaunchRequest(
            host=self.host,
            port=self.port,
            cors_origins=self.cors_origins,
            token=self.token,
            workspace=workspace,
            explicit_workspace=True,
            ramdisk_root=self.ramdisk_root,
            log_level=self.log_level,
            debug_tracing=self.debug_tracing,
            self_upgrade_mode=self.self_upgrade_mode,
            config_snapshot=self.config_snapshot,
        )

    def get_effective_token(self) -> str:
        """Get authentication token (from explicit or generate new).

        Returns:
            Authentication token string
        """
        if self.token:
            return self.token
        if self.config_snapshot:
            token = self.config_snapshot.get("security.token")
            if token:
                return str(token)
        # Generate random token
        import secrets

        return secrets.token_urlsafe(32)

    def get_effective_cors_origins(self) -> list[str]:
        """Get effective CORS origins list.

        Returns:
            List of CORS origin URLs
        """
        if self.cors_origins:
            return self.cors_origins
        # Default origins
        return [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:49977",
        ]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary representation of this request
        """
        return {
            "host": self.host,
            "port": self.port,
            "cors_origins": self.cors_origins,
            "token": "***" if self.token else None,  # Mask token
            "workspace": str(self.workspace),
            "explicit_workspace": self.explicit_workspace,
            "ramdisk_root": str(self.ramdisk_root) if self.ramdisk_root else None,
            "log_level": self.log_level,
            "debug_tracing": self.debug_tracing,
            "self_upgrade_mode": self.self_upgrade_mode,
        }


@dataclass(frozen=True)
class BackendLaunchResult:
    """Result of backend launch attempt.

    Attributes:
        success: Whether the launch was successful
        port: The port the server is listening on (if successful)
        process_handle: Handle to the server process (if successful)
        error_message: Error message (if failed)
        startup_time_ms: Time taken to start in milliseconds
        config_snapshot: Configuration snapshot used for launch
    """

    success: bool
    port: int | None = None
    process_handle: Any | None = None
    error_message: str | None = None
    startup_time_ms: int = 0
    config_snapshot: Any | None = None

    def is_success(self) -> bool:
        """Check if launch was successful.

        Returns:
            True if launch succeeded and process handle exists
        """
        return self.success and self.process_handle is not None

    def get_error(self) -> str:
        """Get formatted error message.

        Returns:
            Error message or empty string if successful
        """
        if self.error_message:
            return self.error_message
        if not self.success:
            return "Unknown launch failure"
        return ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization.

        Returns:
            Dictionary suitable for JSON serialization
        """
        return {
            "success": self.success,
            "port": self.port,
            # BUG-008 fix: process_handle may be a UvicornServerHandle that
            # does not expose .pid; use getattr to avoid AttributeError.
            "pid": getattr(self.process_handle, "pid", None) if self.process_handle else None,
            "error": self.error_message,
            "startup_time_ms": self.startup_time_ms,
        }

    def to_electron_event(self) -> dict[str, Any]:
        """Convert to Electron backend_started event format.

        This maintains compatibility with the existing Electron protocol.

        Returns:
            Dictionary matching the backend_started event format
        """
        import time

        return {
            "event": "backend_started",
            "port": self.port,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "success": self.success,
        }


if __name__ == "__main__":
    # Basic test
    req = BackendLaunchRequest(port=8080)
    logger.info("Request: %s", req)
    logger.info("Uvicorn options: %s", req.to_uvicorn_options())

    result = BackendLaunchResult(success=True, port=8080)
    logger.info("Result: %s", result.to_dict())
    logger.info("Electron event: %s", result.to_electron_event())
