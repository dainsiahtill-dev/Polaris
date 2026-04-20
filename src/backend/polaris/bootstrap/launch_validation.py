"""Application bootstrap with unified configuration validation.

This module provides the unified configuration validation entry point
and bootstrap utilities for the Polaris backend server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LaunchValidationResult:
    """Result of configuration validation for bootstrap launch.

    Attributes:
        errors: List of validation error messages
        warnings: List of validation warning messages

    Note: This is distinct from other ValidationResult types:
    - ToolArgValidationResult: Tool argument validation
    - ProviderConfigValidationResult: Provider configuration validation
    - FileOpValidationResult: File operation validation
    - SchemaValidationResult: Schema validation
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Check if validation passed (no errors)."""
        return len(self.errors) == 0

    def add_error(self, message: str) -> LaunchValidationResult:
        """Add an error message and return self for chaining."""
        self.errors.append(message)
        return self

    def add_warning(self, message: str) -> LaunchValidationResult:
        """Add a warning message and return self for chaining."""
        self.warnings.append(message)
        return self


# Backward compatibility alias (deprecated)
ValidationResult = LaunchValidationResult


def validate_launch_request(request: Any) -> ValidationResult:
    """Unified configuration validation entry point.

    This function performs all configuration validation in one place,
    moving validation out of __post_init__ and into the bootstrap phase.

    Args:
        request: BackendLaunchRequest or any object with workspace/port attributes

    Returns:
        ValidationResult with errors and warnings
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Validate workspace if provided
    if hasattr(request, "workspace") and request.workspace:
        workspace_path = Path(request.workspace)
        if not workspace_path.exists():
            errors.append(f"Workspace not found: {request.workspace}")
        elif not workspace_path.is_dir():
            errors.append(f"Workspace is not a directory: {request.workspace}")
        else:
            # Check writable (best effort)
            try:
                test_file = workspace_path / ".write_test"
                test_file.write_text("")
                test_file.unlink()
            except (OSError, PermissionError):
                warnings.append(f"Workspace may not be writable: {request.workspace}")

    # Validate port if provided
    if hasattr(request, "port") and request.port is not None:
        port = request.port
        if not (0 <= port <= 65535):
            errors.append(f"Invalid port: {port} (must be 0-65535)")
        elif port != 0 and not (1024 <= port <= 65535):
            # Port 0 is allowed for auto-selection
            # Ports below 1024 require elevated privileges
            warnings.append(f"Port {port} may require elevated privileges")

    # Validate host if provided
    if hasattr(request, "host") and request.host:
        host = request.host.strip()
        if not host:
            errors.append("Host cannot be empty")
        # Basic host validation - could be extended
        banned_hosts = ["0.0.0.0", "::", ""]
        if host in banned_hosts and host != "":
            warnings.append(f"Host '{host}' binds to all interfaces - ensure this is intentional")

    # Validate ramdisk_root if provided
    if hasattr(request, "ramdisk_root") and request.ramdisk_root:
        ramdisk_path = Path(request.ramdisk_root)
        if not ramdisk_path.exists():
            errors.append(f"Ramdisk root does not exist: {request.ramdisk_root}")
        elif not ramdisk_path.is_dir():
            errors.append(f"Ramdisk root is not a directory: {request.ramdisk_root}")

    # Validate log_level if provided
    if hasattr(request, "log_level") and request.log_level:
        valid_levels = {"debug", "info", "warning", "error", "critical"}
        if request.log_level.lower() not in valid_levels:
            errors.append(f"Invalid log level: {request.log_level}")

    # Validate cors_origins if provided
    if hasattr(request, "cors_origins") and request.cors_origins:
        for origin in request.cors_origins:
            if not origin.startswith(("http://", "https://")):
                warnings.append(f"CORS origin '{origin}' may be missing protocol scheme")

    return ValidationResult(errors=errors, warnings=warnings)


def validate_environment() -> ValidationResult:
    """Validate the runtime environment.

    Returns:
        ValidationResult with environment-related errors and warnings
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Check Python version

    # Check UTF-8 environment
    encoding = os.environ.get("PYTHONIOENCODING", "").lower()
    if encoding and "utf-8" not in encoding:
        warnings.append(f"PYTHONIOENCODING is set to '{encoding}', UTF-8 recommended")

    # Check required environment variables
    required_vars: list[str] = []
    for var in required_vars:
        if not os.environ.get(var):
            warnings.append(f"Environment variable {var} is not set")

    return ValidationResult(errors=errors, warnings=warnings)


def bootstrap_validation(request: Any) -> ValidationResult:
    """Run all validation checks during bootstrap phase.

    This is the main entry point for validation during server bootstrap.
    It combines environment validation with request validation.

    Args:
        request: BackendLaunchRequest or compatible object

    Returns:
        ValidationResult with all errors and warnings
    """
    # Validate environment first
    env_result = validate_environment()

    # Validate request
    request_result = validate_launch_request(request)

    # Combine results
    return ValidationResult(
        errors=env_result.errors + request_result.errors,
        warnings=env_result.warnings + request_result.warnings,
    )


# BootstrapError is defined in polaris.kernelone.errors for consistency
# Import here for backwards compatibility
from polaris.kernelone.errors import BootstrapError  # noqa: F401


def ensure_utf8_environment() -> None:
    """Ensure UTF-8 encoding environment is set up.

    Polaris requires explicit UTF-8 for all text handling.
    """
    # Force UTF-8 mode for Python
    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"

    # Platform-specific settings
    if os.name == "nt":  # Windows
        os.environ["CHCP"] = "65001"
