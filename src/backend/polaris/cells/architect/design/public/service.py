"""Stable public service exports for `architect.design` cell."""

from __future__ import annotations

import hashlib
import json

from polaris.cells.architect.design.internal.architect_agent import ArchitectAgent
from polaris.cells.architect.design.internal.architect_service import (
    ArchitectConfig,
    ArchitectService,
    ArchitectureDoc,
)
from polaris.cells.architect.design.public.contracts import (
    ArchitectDesignErrorV1,
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
)


def _stable_design_id(command: GenerateArchitectureDesignCommandV1) -> str:
    payload = {
        "workspace": command.workspace,
        "objective": command.objective,
        "constraints": command.constraints,
        "context": command.context,
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return f"boundary-{digest[:16]}"


def generate_architecture_design(command: GenerateArchitectureDesignCommandV1) -> ArchitectureDesignResultV1:
    """Generate a typed architecture design result for a public command."""
    if not isinstance(command, GenerateArchitectureDesignCommandV1):
        raise TypeError("command must be a GenerateArchitectureDesignCommandV1")

    target_cell = str(command.context.get("target_cell") or "").strip()
    changed_paths = tuple(str(path) for path in command.context.get("changed_paths", ()) if str(path).strip())
    summary_target = target_cell or "unspecified cell"
    summary = f"Boundary validation prepared for {summary_target}; {len(changed_paths)} changed path(s) were supplied."
    try:
        return ArchitectureDesignResultV1(
            ok=True,
            workspace=command.workspace,
            design_id=_stable_design_id(command),
            status="completed",
            summary=summary,
            recommendation_paths=("runtime/state/architect/boundary-validation.json",),
        )
    except ValueError as exc:
        raise ArchitectDesignErrorV1(str(exc), code="invalid_architecture_design_result") from exc


__all__ = [
    "ArchitectAgent",
    "ArchitectConfig",
    "ArchitectService",
    "ArchitectureDoc",
    "generate_architecture_design",
]
