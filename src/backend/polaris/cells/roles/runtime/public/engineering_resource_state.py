"""Read-only engineering lifecycle facts; no execution or acceptance authority."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from polaris.cells.roles.runtime.internal.engineering_session import query_resource_state


def query_engineering_resource_state(
    workspace: Path | str, session_id: str | None = None, request_key: str | None = None
) -> dict[str, Any]:
    return cast("dict[str, Any]", (query_resource_state(workspace, session_id=session_id, request_key=request_key)))
