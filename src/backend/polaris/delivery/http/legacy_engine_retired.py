"""Explicit HTTP retirement contract for obsolete execution adapters."""

from fastapi import HTTPException


def legacy_engine_retired(operation: str) -> HTTPException:
    """Reject an old write without constructing its runtime or storage owner."""
    return HTTPException(
        status_code=410,
        detail={
            "error_code": "legacy_engine_retired",
            "message": f"{operation} is retired; use engineering runs for execution or role chat for discussion.",
            "supported_endpoints": [
                "/v2/engineering/runs",
                "/v2/engineering/runs/{session_id}",
                "/v2/role/{role}/chat",
            ],
        },
    )
