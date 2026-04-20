"""Tool chain data models for KernelOne tool execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolChainStep:
    """Enhanced tool step with chain execution metadata."""

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    step_id: str | None = None
    save_as: str | None = None  # Store result under this key for later steps
    input_from: str | None = None  # Use result from this key as input
    on_error: str = "stop"  # Literal["stop", "retry", "continue"]
    max_retries: int = 2
    retry_count: int = 0


@dataclass
class ToolChainResult:
    """Result of a tool chain execution."""

    ok: bool
    outputs: list[dict[str, Any]]
    errors: list[str]
    total_steps: int
    completed_steps: int
    failed_steps: int
    retried_steps: int
    saved_results: dict[str, Any] = field(default_factory=dict)
