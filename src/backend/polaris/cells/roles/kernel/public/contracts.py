"""Public contracts for `roles.kernel` cell."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


@dataclass(frozen=True)
class BuildRolePromptCommandV1:
    role_id: str
    workspace: str
    context: Mapping[str, Any] = field(default_factory=dict)
    structured_output: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class ParseRoleOutputCommandV1:
    role_id: str
    output: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "output", _require_non_empty("output", self.output))


@dataclass(frozen=True)
class CheckRoleQualityCommandV1:
    role_id: str
    output: str
    context: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "output", _require_non_empty("output", self.output))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class ClassifyKernelErrorQueryV1:
    error_text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_text", _require_non_empty("error_text", self.error_text))


@dataclass(frozen=True)
class ResolveRetryPolicyQueryV1:
    error_text: str
    attempt: int = 1
    max_retries: int = 3

    def __post_init__(self) -> None:
        object.__setattr__(self, "error_text", _require_non_empty("error_text", self.error_text))
        if self.attempt < 1:
            raise ValueError("attempt must be >= 1")
        if self.max_retries < 1:
            raise ValueError("max_retries must be >= 1")


@dataclass(frozen=True)
class ExecuteRoleKernelTurnCommandV1:
    role_id: str
    workspace: str
    prompt: str
    context: Mapping[str, Any] = field(default_factory=dict)
    structured_output: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "prompt", _require_non_empty("prompt", self.prompt))
        object.__setattr__(self, "context", _to_dict_copy(self.context))


@dataclass(frozen=True)
class RoleKernelPromptBuiltEventV1:
    event_id: str
    role_id: str
    workspace: str
    built_at: str
    template_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "built_at", _require_non_empty("built_at", self.built_at))
        if self.template_id is not None:
            object.__setattr__(self, "template_id", _require_non_empty("template_id", self.template_id))


@dataclass(frozen=True)
class RoleKernelParsedOutputEventV1:
    event_id: str
    role_id: str
    workspace: str
    parsed_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "parsed_at", _require_non_empty("parsed_at", self.parsed_at))


@dataclass(frozen=True)
class RoleKernelQualityCheckedEventV1:
    event_id: str
    role_id: str
    workspace: str
    checked_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_non_empty("event_id", self.event_id))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "checked_at", _require_non_empty("checked_at", self.checked_at))


@dataclass(frozen=True)
class GenericRoleResponse:
    """Type-safe fallback response schema for structured output parsing.

    This dataclass provides immutable, type-safe response parsing when
    Instructor schemas are unavailable. It captures:
    - content: The primary text output from the role
    - tool_calls: Optional list of tool call dictionaries
    - metadata: Additional context or metadata about the response

    All fields are immutable (frozen=True) to ensure thread-safety and
    prevent accidental mutation during downstream processing.
    """

    content: str
    tool_calls: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for downstream compatibility.

        Returns:
            Dictionary representation matching the expected output format.
        """
        return asdict(self)


@dataclass(frozen=True)
class RoleKernelResultV1:
    ok: bool
    status: str
    role_id: str
    workspace: str
    prompt: str = ""
    parsed: Mapping[str, Any] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _require_non_empty("status", self.status))
        object.__setattr__(self, "role_id", _require_non_empty("role_id", self.role_id))
        object.__setattr__(self, "workspace", _require_non_empty("workspace", self.workspace))
        object.__setattr__(self, "parsed", _to_dict_copy(self.parsed))
        object.__setattr__(self, "quality", _to_dict_copy(self.quality))
        if not self.ok and not (self.error_code or self.error_message):
            raise ValueError("failed result must include error_code or error_message")


class RoleKernelError(RuntimeError):
    """Structured contract error for `roles.kernel`."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "roles_kernel_error",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(_require_non_empty("message", message))
        self.code = _require_non_empty("code", code)
        self.details = _to_dict_copy(details)

    def to_dict(self) -> dict[str, Any]:
        """Return structured error dict matching ADR-003 {code, message, details}.

        ADR-003: All Error classes must implement to_dict().
        """
        return {
            "code": self.code,
            "message": str(self),
            "details": dict(self.details),
        }


@runtime_checkable
class ToolGatewayPort(Protocol):
    """Port protocol for tool gateway abstraction.

    This protocol enables dependency injection of tool execution backends,
    allowing the kernel to work with any conforming implementation
    (e.g., RoleToolGateway, mock implementations for testing).

    Example:
        >>> class MockToolGateway:
        ...     async def execute(self, tool_name: str, args: dict) -> dict:
        ...         return {"success": True, "result": "mocked"}
        ...     def requires_approval(self, tool_name: str, **kwargs) -> bool:
        ...         return False
        >>>
        >>> kernel = RoleExecutionKernel(tool_gateway=MockToolGateway())
    """

    def execute(self, tool_name: str, args: dict) -> dict[str, Any]:
        """Execute a tool by name with given arguments.

        Args:
            tool_name: Name of the tool to execute.
            args: Dictionary of arguments to pass to the tool.

        Returns:
            Result dictionary with at least 'success' key.
        """
        ...

    def requires_approval(
        self,
        tool_name: str,
        args: dict | None = None,
        state: Any | None = None,
    ) -> bool:
        """Check if a tool call requires user approval.

        Args:
            tool_name: Name of the tool to check.
            args: Optional tool arguments for context-aware checks.
            state: Optional execution state for policy evaluation.

        Returns:
            True if approval is required, False otherwise.
        """
        ...


@runtime_checkable
class IRoleKernelService(Protocol):
    def build_prompt(self, command: BuildRolePromptCommandV1) -> Mapping[str, Any]:
        """Build a prompt payload."""

    def parse_output(self, command: ParseRoleOutputCommandV1) -> Mapping[str, Any]:
        """Parse one role output."""

    def check_quality(self, command: CheckRoleQualityCommandV1) -> Mapping[str, Any]:
        """Check output quality."""

    def classify_error(self, query: ClassifyKernelErrorQueryV1) -> Any:
        """Classify one kernel error."""

    def resolve_retry_policy(self, query: ResolveRetryPolicyQueryV1) -> Mapping[str, Any]:
        """Resolve retry decision."""


__all__ = [
    "BuildRolePromptCommandV1",
    "CheckRoleQualityCommandV1",
    "ClassifyKernelErrorQueryV1",
    "ExecuteRoleKernelTurnCommandV1",
    "GenericRoleResponse",
    "IRoleKernelService",
    "ParseRoleOutputCommandV1",
    "ResolveRetryPolicyQueryV1",
    "RoleKernelError",
    "RoleKernelParsedOutputEventV1",
    "RoleKernelPromptBuiltEventV1",
    "RoleKernelQualityCheckedEventV1",
    "RoleKernelResultV1",
    "ToolGatewayPort",
]
