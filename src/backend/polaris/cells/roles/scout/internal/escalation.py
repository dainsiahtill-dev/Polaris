"""Escalate a fuzzy probe to a governed scout role-session (read-only) (UTF-8).

When the deterministic probe is too weak to be useful, the caller may opt in to
an LLM-backed reconnaissance turn by setting ``allow_escalation=True`` on the
:class:`ScoutProbeTargetV1`. This bridge runs the ``scout`` role through the
canonical :class:`RoleRuntimeService` entry point and returns its output text.

Read-only is enforced by the scout profile (``allow_code_write=False``) plus the
sandbox policy at the kernel; this bridge adds no write capability of its own.
"""

from __future__ import annotations

from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1


async def escalate_probe(target: ScoutProbeTargetV1, workspace: str) -> str:
    """Run scout via ``execute_role_session`` and return the role output text.

    Args:
        target: The probe target whose ``query`` becomes the user message.
        workspace: The workspace root the scout role operates over.

    Returns:
        The stripped ``output`` text produced by the scout role session.
    """
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    command = ExecuteRoleSessionCommandV1(
        role="scout",
        session_id=f"scout-probe-{target.cache_key()}",
        workspace=str(workspace or "."),
        user_message=target.query,
        domain="code",
        stream=False,
        metadata={
            "source": "roles.scout.escalation",
            "read_only": True,
            "fallback_policy": "fail_closed",
        },
    )
    result = await RoleRuntimeService().execute_role_session(command)
    return str(getattr(result, "output", "") or "").strip()
