"""Director (Director) Output Schema - Code implementation.

Defines the structured output format for code execution and patch generation.
"""

from typing import Literal

from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous_command,
    is_path_traversal,
)
from pydantic import BaseModel, Field, field_validator

from .base import ToolCall


class PatchOperation(BaseModel):
    """Single code patch operation."""

    file: str = Field(..., description="Target file path (relative)")
    search: str = Field(default="", description="Text to search for (empty for new files)")
    replace: str = Field(..., description="Replacement text or full file content")
    description: str | None = Field(default=None, description="Brief description of the change")

    @field_validator("file")
    @classmethod
    def validate_file_path(cls, v: str) -> str:
        """Ensure file path is safe."""
        if not v:
            raise ValueError("File path cannot be empty")
        if v.startswith("/"):
            raise ValueError(f"File path must be relative, got: {v}")
        if is_path_traversal(v):
            raise ValueError(f"File path contains traversal pattern: {v}")
        if is_dangerous_command(v):
            raise ValueError(f"Potentially dangerous path: {v}")
        return v


class DirectorValidationResult(BaseModel):
    """Validation/test result for Director execution.

    Note: This is a Pydantic model distinct from other ValidationResult dataclasses:
    - ToolArgValidationResult: Tool argument validation (dataclass)
    - ProviderConfigValidationResult: Provider configuration validation (dataclass)
    - FileOpValidationResult: File operation validation (dataclass)
    - LaunchValidationResult: Bootstrap launch validation (dataclass)
    - SchemaValidationResult: Schema validation (dataclass)
    """

    passed: bool = Field(...)
    command: str | None = Field(default=None)
    output: str | None = Field(default=None)
    error: str | None = Field(default=None)


# Backward compatibility alias (deprecated)
ValidationResult = DirectorValidationResult


class DirectorOutput(BaseModel):
    """Director structured output - Implementation plan and patches.

    This model ensures LLM outputs conform to the expected implementation format.
    """

    mode: Literal["patch", "tool_calls", "mixed"] = Field(..., description="Execution mode")

    summary: str = Field(..., min_length=10, max_length=500, description="Summary of changes made")

    patches: list[PatchOperation] = Field(default_factory=list, description="Code patches to apply")

    tool_calls: list[ToolCall] = Field(default_factory=list, description="Tool calls to execute")

    validation: ValidationResult | None = Field(default=None, description="Validation/test results if any")

    next_steps: list[str] = Field(default_factory=list, description="Recommended next steps")

    @field_validator("patches")
    @classmethod
    def validate_patches(cls, v: list[PatchOperation]) -> list[PatchOperation]:
        """Ensure patches are valid."""
        seen_files = set()
        for patch in v:
            # Check for duplicate files with same search
            key = f"{patch.file}:{patch.search[:50]}"
            if key in seen_files:
                raise ValueError(f"Duplicate patch for file: {patch.file}")
            seen_files.add(key)
        return v

    def model_post_init(self, __context) -> None:
        """Validate mode consistency."""
        if self.mode == "patch" and not self.patches:
            raise ValueError("Mode 'patch' requires at least one patch")

        if self.mode == "tool_calls" and not self.tool_calls:
            raise ValueError("Mode 'tool_calls' requires at least one tool call")

        if self.mode == "mixed" and not (self.patches or self.tool_calls):
            raise ValueError("Mode 'mixed' requires patches or tool calls")
