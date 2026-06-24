"""Platform capability token contracts for Run Ledger events."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class JobToken:
    """Immutable capability token carried across PM/CE/Director/QA projections."""

    schema_version: int
    token_id: str
    run_id: str
    factory_run_id: str
    project_id: str
    stage: str
    target_files: list[str] = field(default_factory=list)
    allowed_paths: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    gate_policy: dict[str, Any] = field(default_factory=dict)
    capability_audit: dict[str, Any] = field(default_factory=dict)
    parent_token_id: str = ""
    repair_lineage: list[dict[str, Any]] = field(default_factory=list)
    contract_hash: str = ""
    blueprint_hash: str = ""
    source: str = "control_plane.job_token"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation for ledger embedding."""

        return asdict(self)


__all__ = ["JobToken"]
