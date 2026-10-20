"""Small program interfaces for the engineering runtime, independent of legacy runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class RoleRunCommand:
    session_id: str
    role: Literal["pm", "chief_engineer", "director", "qa"]
    instruction: str
    workspace: Path
    workflow: Literal["direct", "collaborative"] = "direct"
    required_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionScope:
    read_roots: tuple[Path, ...]
    write_roots: tuple[Path, ...]
    enabled_tools: frozenset[str]
    network_allowed: bool


@dataclass(frozen=True)
class RunLimits:
    deadline: float | None
    max_output_bytes: int
    max_tool_output_bytes: int


@dataclass(frozen=True)
class ToolObservation:
    call_id: str
    tool: str
    ok: bool
    content: str
    exit_code: int | None
    interrupted: bool


@dataclass(frozen=True)
class RoleRunResult:
    session_id: str
    role: str
    text: str
    status: Literal["completed", "failed", "cancelled", "interrupted"]
    verification: Literal["passed", "failed", "not_run", "unknown"]
    checks: tuple[ToolObservation, ...]
