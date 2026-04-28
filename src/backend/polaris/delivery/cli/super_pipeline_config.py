"""SUPER Mode Pipeline Configuration — StageConstraint, PipelineStage, SuperPipelineConfig.

This module defines the data-driven configuration for SUPER mode pipeline.
All constraints (exploration limits, tool_choice, forbidden tools) are declared
here and injected into both system prompts and API requests.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from polaris.delivery.cli.super_mode import SuperPipelineContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# StageConstraint — per-stage LLM behavior constraints
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageConstraint:
    """Declarative constraint for a pipeline stage.

    Constraints are injected into:
    A. System prompt (for LLM awareness)
    B. API request (for hard enforcement, e.g. tool_choice)
    """

    max_exploration_turns: int = 0
    """Max turns allowed for exploration tools (repo_tree, glob, etc.).
    0 = forbidden. -1 = unlimited."""

    tool_choice: str | dict[str, Any] = "auto"
    """LLM API tool_choice parameter: 'auto', 'required', or function spec."""

    forbidden_tools: tuple[str, ...] = ()
    """Tool names that must NOT be called in this stage."""

    delivery_mode: str = "analyze_only"
    """analyze_only | materialize_changes"""

    force_write_on_timeout: bool = False
    """If True, switch to tool_choice='required' after phase timeout."""

    def to_prompt_text(self) -> str:
        """Generate constraint instructions for system prompt."""
        lines: list[str] = []
        if self.max_exploration_turns == 0 and self.forbidden_tools:
            tool_list = ", ".join(self.forbidden_tools)
            lines.append(f"CRITICAL: Do NOT call {tool_list} or any exploration tools.")
        elif self.max_exploration_turns > 0:
            lines.append(
                f"Use exploration tools (repo_tree, glob) at most {self.max_exploration_turns} time(s). "
                "Then produce your analysis."
            )
        if self.tool_choice == "required":
            lines.append("You MUST call at least one tool. Text-only responses are invalid.")
        if self.delivery_mode == "materialize_changes":
            lines.append(
                "Your ONLY job is to EXECUTE code modifications. "
                "Start modifying files IMMEDIATELY using write_file, edit_file, or append_to_file. "
                "Do NOT output analysis or plans."
            )
        return "\n".join(lines)

    def to_api_tool_choice(self) -> Any:
        """Return the value to pass as tool_choice in the LLM API request."""
        return self.tool_choice


# ---------------------------------------------------------------------------
# PipelineStage — single stage definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """Definition of one stage in the SUPER pipeline."""

    role: str
    """Role name: architect, pm, chief_engineer, director."""

    handoff_builder: Callable[..., str]
    """Function that builds the handoff message for this stage.
    Signature: (original_request=, architect_output=, ...) -> str"""

    handoff_kwargs: dict[str, str] = field(default_factory=dict)
    """Fixed kwargs to pass to handoff_builder (besides context-derived ones)."""

    constraint: StageConstraint = field(default_factory=StageConstraint)
    """LLM behavior constraints for this stage."""

    max_retries: int = 1
    """Number of retries on failure."""

    timeout_seconds: int = 300
    """Per-stage timeout."""

    skip_condition: Callable[[SuperPipelineContext], bool] | None = None
    """If returns True, skip this stage entirely."""

    on_failure: Literal["retry", "skip", "degrade", "abort"] = "retry"
    """Action when all retries are exhausted."""


# ---------------------------------------------------------------------------
# StageResult / PipelineResult — execution outcomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StageResult:
    """Result of executing a single pipeline stage."""

    role: str
    success: bool
    content: str = ""
    error: str | None = None
    retry_count: int = 0
    duration_seconds: float = 0.0
    llm_calls: int = 0
    tool_calls: int = 0
    skipped: bool = False
    degraded: bool = False


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Result of the full SUPER pipeline execution."""

    stages: tuple[StageResult, ...]
    final_role: str
    total_duration_seconds: float = 0.0
    saw_error: bool = False

    @property
    def completed_roles(self) -> tuple[str, ...]:
        return tuple(s.role for s in self.stages if s.success)

    @property
    def failed_roles(self) -> tuple[str, ...]:
        return tuple(s.role for s in self.stages if not s.success and not s.skipped)

    def stage_for(self, role: str) -> StageResult | None:
        for s in self.stages:
            if s.role == role:
                return s
        return None


# ---------------------------------------------------------------------------
# SuperPipelineConfig — full pipeline configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SuperPipelineConfig:
    """Complete configuration for a SUPER mode pipeline run."""

    stages: tuple[PipelineStage, ...]
    max_total_duration_seconds: int = 1200
    orchestrator_mode: Literal["session_orchestrator", "stream_chat"] = "session_orchestrator"
    persist_blueprints: bool = True


# ---------------------------------------------------------------------------
# Default configuration
# ---------------------------------------------------------------------------


def _pm_not_empty(ctx: SuperPipelineContext) -> bool:
    """Skip CE if PM produced no output."""
    return not ctx.pm_output.strip()


def _build_architect_handoff(**kw: Any) -> str:
    from polaris.delivery.cli.super_mode import build_super_readonly_message

    return build_super_readonly_message(
        role="architect",
        original_request=kw.get("original_request", ""),
    )


def _build_pm_handoff(**kw: Any) -> str:
    from polaris.delivery.cli.super_mode import build_pm_handoff_message

    return build_pm_handoff_message(
        original_request=kw.get("original_request", ""),
        architect_output=kw.get("architect_output", ""),
        blueprint_file_path=kw.get("blueprint_file_path", ""),
    )


def _build_ce_handoff(**kw: Any) -> str:
    from polaris.delivery.cli.super_mode import build_chief_engineer_handoff_message

    return build_chief_engineer_handoff_message(
        original_request=kw.get("original_request", ""),
        architect_output=kw.get("architect_output", ""),
        pm_output=kw.get("pm_output", ""),
        claimed_tasks=list(kw.get("claimed_tasks", [])),
    )


def _build_director_handoff(**kw: Any) -> str:
    from polaris.delivery.cli.super_mode import build_director_task_handoff_message

    return build_director_task_handoff_message(
        original_request=kw.get("original_request", ""),
        architect_output=kw.get("architect_output", ""),
        pm_output=kw.get("pm_output", ""),
        claimed_tasks=list(kw.get("claimed_tasks", [])),
        blueprint_items=list(kw.get("blueprint_items", [])),
    )


DEFAULT_SUPER_PIPELINE = SuperPipelineConfig(
    stages=(
        PipelineStage(
            role="architect",
            handoff_builder=_build_architect_handoff,
            constraint=StageConstraint(
                max_exploration_turns=1,
                tool_choice="auto",
                forbidden_tools=(),
                delivery_mode="analyze_only",
            ),
            max_retries=1,
            timeout_seconds=180,
        ),
        PipelineStage(
            role="pm",
            handoff_builder=_build_pm_handoff,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="auto",
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="analyze_only",
            ),
            max_retries=2,
            timeout_seconds=240,
            on_failure="retry",
        ),
        PipelineStage(
            role="chief_engineer",
            handoff_builder=_build_ce_handoff,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="auto",
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="analyze_only",
            ),
            max_retries=1,
            timeout_seconds=180,
            skip_condition=_pm_not_empty,
            on_failure="skip",
        ),
        PipelineStage(
            role="director",
            handoff_builder=_build_director_handoff,
            constraint=StageConstraint(
                max_exploration_turns=0,
                tool_choice="required",
                forbidden_tools=("repo_tree", "glob", "list_directory", "repo_rg"),
                delivery_mode="materialize_changes",
                force_write_on_timeout=True,
            ),
            max_retries=1,
            timeout_seconds=600,
            on_failure="degrade",
        ),
    ),
)


__all__ = [
    "DEFAULT_SUPER_PIPELINE",
    "PipelineResult",
    "PipelineStage",
    "StageConstraint",
    "StageResult",
    "SuperPipelineConfig",
]
