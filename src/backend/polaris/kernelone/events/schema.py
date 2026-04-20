from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

EventKind = Literal["action", "observation"]
Actor = Literal["PM", "Director", "Reviewer", "QA", "System", "Tooling"]
Phase = Literal[
    "handoff",
    "receipt",
    "tool_plan",
    "tool_exec",
    "evidence",
    "patch_plan",
    "apply",
    "rollback",
    "review",
    "qa",
    "gap_review",
    "memory",
    "done",
]


class EventRef(BaseModel):
    task_id: str | None = None
    task_fingerprint: str | None = None
    run_id: str | None = None
    pm_iteration: int | None = None
    director_iteration: int | None = None
    phase: Phase | None = None
    files: list[str] | None = None
    evidence_path: str | None = None
    trajectory_path: str | None = None


class Truncation(BaseModel):
    truncated: bool = False
    reason: str | None = None
    original_bytes: int | None = None
    kept_bytes: int | None = None
    original_lines: int | None = None
    kept_lines: int | None = None
    # Continuation-related fields (new)
    continuation_attempt: int = 0
    continuation_success: bool = False
    blocked: bool = False


class EventBase(BaseModel):
    schema_version: int = 1
    ts: str
    ts_epoch: float
    seq: int
    event_id: str
    kind: EventKind
    actor: Actor
    name: str
    refs: EventRef = Field(default_factory=EventRef)
    summary: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)


class ActionEvent(EventBase):
    kind: Literal["action"] = "action"
    input: dict[str, Any] = Field(default_factory=dict)


class ObservationEvent(EventBase):
    kind: Literal["observation"] = "observation"
    ok: bool = True
    output: dict[str, Any] = Field(default_factory=dict)
    truncation: Truncation = Field(default_factory=Truncation)
    duration_ms: int | None = None
    error: str | None = None
