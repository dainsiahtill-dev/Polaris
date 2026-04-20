"""Context engine data models.

Architecture note (P1-CTX-001 convergence):
    This module provides Pydantic models for serialization/deserialization only.
    The canonical ContextBudget definition is in polaris.kernelone.context.contracts.

    - contracts.ContextBudget: canonical dataclass for allocation limits
    - models.ContextBudget: Pydantic model for serialization (serde) compatibility
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from polaris.kernelone.utils.time_utils import _utc_now
from pydantic import BaseModel, Field


class ContextBudget(BaseModel):
    """Pydantic model for ContextBudget serialization.

    Note: This mirrors contracts.ContextBudget fields but uses Pydantic for
    serialization compatibility. The canonical source of truth is
    polaris.kernelone.context.contracts.ContextBudget.
    """

    max_tokens: int = 0
    max_chars: int = 0
    cost_class: str = "LOCAL"


class ContextRequest(BaseModel):
    run_id: str
    step: int
    role: str
    mode: str
    task_id: str | None = None
    query: str
    budget: ContextBudget
    sources_enabled: list[str] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)
    events_path: str | None = None
    compact_now: bool = False
    compact_focus: str = ""
    task_identity: dict[str, Any] = Field(default_factory=dict)


class ContextItem(BaseModel):
    id: str = Field(default_factory=lambda: f"ctx_{uuid4()}")
    kind: str
    content_or_pointer: str
    refs: dict[str, Any] = Field(default_factory=dict)
    size_est: int = 0
    priority: int = 5
    reason: str = ""
    provider: str = ""


class ContextPack(BaseModel):
    request_hash: str
    items: list[ContextItem] = Field(default_factory=list)
    compression_log: list[dict[str, Any]] = Field(default_factory=list)
    rendered_prompt: str = ""
    rendered_messages: list[dict[str, Any]] = Field(default_factory=list)
    total_tokens: int = 0
    total_chars: int = 0
    build_timestamp: datetime = Field(default_factory=_utc_now)
    snapshot_path: str = ""
    snapshot_hash: str = ""
