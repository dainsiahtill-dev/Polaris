"""Configuration and bootstrap errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class ConfigurationError(KernelOneError):
    """Configuration-related errors.

    Raised when configuration is invalid, missing, or cannot be loaded.
    These errors are typically non-retryable without fixing the configuration.

    Attributes:
        field: The configuration field that is invalid.
        config_path: Path to the configuration file, if applicable.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CONFIG_ERROR",
        field: str = "",
        config_path: str = "",
        **kwargs,
    ) -> None:
        kwargs.setdefault("retryable", False)
        super().__init__(message, code=code, **kwargs)
        self.field = field
        self.config_path = config_path
        if field:
            self.details["field"] = field
        if config_path:
            self.details["config_path"] = config_path


class ConfigLoadError(ConfigurationError):
    """Configuration loading failed.

    Raised when configuration file cannot be read or parsed.
    """

    def __init__(
        self,
        message: str,
        *,
        config_path: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="CONFIG_LOAD_ERROR",
            config_path=config_path,
            **kwargs,
        )


class ConfigValidationError(ConfigurationError):
    """Configuration validation failed.

    Raised when configuration values fail validation checks.

    Attributes:
        validation_errors: List of specific validation error messages.
    """

    def __init__(
        self,
        message: str,
        *,
        validation_errors: list[str] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(message, code="CONFIG_VALIDATION_ERROR", **kwargs)
        self.validation_errors = validation_errors or []
        if self.validation_errors:
            self.details["validation_errors"] = self.validation_errors


class ConfigMigrationError(ConfigurationError):
    """Configuration migration failed.

    Raised when upgrading or migrating configuration format fails.
    """

    def __init__(
        self,
        message: str,
        *,
        from_version: str = "",
        to_version: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code="CONFIG_MIGRATION_ERROR", **kwargs)
        if from_version:
            self.details["from_version"] = from_version
        if to_version:
            self.details["to_version"] = to_version


class BootstrapError(ConfigurationError):
    """Bootstrap initialization failed."""

    def __init__(
        self,
        message: str,
        *,
        phase: str = "",
        stage: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="BOOTSTRAP_ERROR",
            **kwargs,
        )
        if stage and not phase:
            phase = stage
        if phase:
            self.details["phase"] = phase
        if stage:
            self.details["stage"] = stage


class BackendBootstrapError(BootstrapError):
    """Backend bootstrap failed."""
