"""Shared public role contracts for PM, Chief Engineer, and Director views."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

ROLE_TASK_STATUS_VALUES: tuple[str, ...] = (
    "PENDING",
    "CLAIMED",
    "RUNNING",
    "BLOCKED",
    "FAILED",
    "COMPLETED",
    "CANCELLED",
)

ROLE_TASK_PRIORITY_VALUES: tuple[str, ...] = (
    "LOW",
    "MEDIUM",
    "HIGH",
    "CRITICAL",
)


class RoleTaskContractV1(BaseModel):
    """Canonical task row consumed by PM/Director desktop workspaces."""

    model_config = ConfigDict(extra="forbid")

    id: str
    subject: str
    description: str = ""
    status: str
    priority: str
    claimed_by: str | None = None
    result: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    goal: str = ""
    acceptance: list[str] = Field(default_factory=list)
    target_files: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    current_file: str | None = None
    error: str | None = None
    worker: str | None = None
    pm_task_id: str | None = None
    blueprint_id: str | None = None
    blueprint_path: str | None = None
    runtime_blueprint_path: str | None = None


class ChiefEngineerBlueprintSummaryV1(BaseModel):
    """Canonical Chief Engineer blueprint list item."""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str
    title: str = ""
    summary: str = ""
    status: str | None = None
    source: str = "runtime/blueprints"
    target_files: list[str] = Field(default_factory=list)
    updated_at: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class ChiefEngineerBlueprintListV1(BaseModel):
    """Canonical Chief Engineer blueprint list response."""

    model_config = ConfigDict(extra="forbid")

    blueprints: list[ChiefEngineerBlueprintSummaryV1]
    total: int


class ChiefEngineerBlueprintDetailV1(BaseModel):
    """Canonical Chief Engineer blueprint detail response."""

    model_config = ConfigDict(extra="forbid")

    blueprint_id: str
    source: str = "runtime/blueprints"
    blueprint: dict[str, Any]


__all__ = [
    "ROLE_TASK_PRIORITY_VALUES",
    "ROLE_TASK_STATUS_VALUES",
    "ChiefEngineerBlueprintDetailV1",
    "ChiefEngineerBlueprintListV1",
    "ChiefEngineerBlueprintSummaryV1",
    "RoleTaskContractV1",
]
