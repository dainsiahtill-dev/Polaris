from pydantic import BaseModel, Field, field_validator
from typing import Any

class NATSConfig(BaseModel):
    """NATS configuration for runtime messaging.

    All configuration values can be overridden via environment variables.
    See field descriptions for corresponding environment variable names.
    """

    enabled: bool = Field(
        default=True,
        description="Enable NATS connectivity (KERNELONE_NATS_ENABLED)",
    )
    required: bool = Field(
        default=True,
        description="NATS connection is required for backend readiness (KERNELONE_NATS_REQUIRED)",
    )
    url: str = Field(
        default="nats://127.0.0.1:4222",
        description="NATS server URL (KERNELONE_NATS_URL)",
    )
    user: str = Field(
        default="",
        description="NATS username (KERNELONE_NATS_USER)",
    )
    password: str = Field(
        default="",
        description="NATS password (KERNELONE_NATS_PASSWORD)",
    )
    connect_timeout_sec: float = Field(
        default=3.0,
        description="Connection timeout in seconds (KERNELONE_NATS_CONNECT_TIMEOUT)",
    )
    reconnect_wait_sec: float = Field(
        default=1.0,
        description="Reconnect wait interval in seconds (KERNELONE_NATS_RECONNECT_WAIT)",
    )
    max_reconnect_attempts: int = Field(
        default=-1,
        description="Max reconnect attempts, -1 for infinite (KERNELONE_NATS_MAX_RECONNECT)",
    )
    stream_name: str = Field(
        default="HP_RUNTIME",
        description="NATS stream name for runtime events (KERNELONE_NATS_STREAM_NAME)",
    )

    @field_validator("connect_timeout_sec", "reconnect_wait_sec", mode="before")
    @classmethod
    def validate_positive_float(cls, value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("max_reconnect_attempts", mode="before")
    @classmethod
    def validate_reconnect_attempts(cls, value: Any) -> int:
        try:
            return int(value)
        except (ValueError, TypeError):
            return -1

    @field_validator("enabled", mode="before")
    @classmethod
    def validate_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "off", "disabled")
        return bool(value)

    @field_validator("required", mode="before")
    @classmethod
    def validate_required(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "off", "disabled")
        return bool(value)

    def get_creds(self) -> tuple[str, str] | None:
        """Get NATS credentials tuple (user, password) if configured."""
        if self.user and self.password:
            return (self.user, self.password)
        return None
