"""Release-readiness rollup route handler for the Chief Engineer v2 router.

Lossless extraction of the Tier-2 capstone GO / NO-GO route from the former
single-file ``chief_engineer`` module. It aggregates the governance surface
through the blueprint public contract; it is not a test-patch target, so a
plain module-level import is safe.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from polaris.cells.chief_engineer.blueprint.public import assess_release_readiness
from polaris.delivery.http.routers._shared import require_auth
from polaris.delivery.http.v2.chief_engineer._router import (
    _governance_workspace,
    _split_csv,
    _validate_blueprint_id,
    router,
)


@router.get("/chief-engineer/release-readiness", dependencies=[Depends(require_auth)])
def get_chief_engineer_release_readiness(
    request: Request,
    workspace: str = "",
    blueprint_ids: str = "",
    libraries: str = "",
) -> dict[str, Any]:
    """Executive GO / NO-GO that aggregates the whole governance surface.

    Optional comma-separated ``blueprint_ids`` (release candidates, each run
    through the Quality Gate) and ``libraries`` (checked against the Tech
    Radar stack policy). Read-only and fail-closed.
    """
    target_workspace = _governance_workspace(request, workspace)
    # Validate every blueprint id at the boundary (defense-in-depth, mirrors the
    # handoff-decision route) so an unvalidated id can never reach the filesystem.
    validated_ids = [_validate_blueprint_id(bid) for bid in _split_csv(blueprint_ids)] or None
    decision = assess_release_readiness(
        target_workspace,
        blueprint_ids=validated_ids,
        libraries=_split_csv(libraries) or None,
    )
    return {"ok": True, "workspace": target_workspace, "readiness": decision.to_dict()}


__all__ = [
    "get_chief_engineer_release_readiness",
]
