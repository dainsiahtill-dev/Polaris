"""Streaming role-turn flow owner."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.kernel.stream_run_id import resolve_stream_run_id
from polaris.cells.roles.kernel.internal.kernel.turn_execution import execute_transaction_kernel_stream
from polaris.cells.roles.kernel.internal.kernel.turn_prompt_setup import build_role_turn_prompt_setup
from polaris.cells.roles.profile.public.service import RoleTurnRequest
from polaris.kernelone.events.uep_publisher import UEPEventPublisher

if TYPE_CHECKING:
    from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel

logger = logging.getLogger(__name__)


async def execute_stream_role_turn(
    *,
    kernel: RoleExecutionKernel,
    role: str,
    request: RoleTurnRequest,
) -> AsyncGenerator[dict[str, Any], None]:
    """Execute a streaming role turn through TransactionKernel.

    Boundary:
        This function owns stream run-id setup and stream event wrapping around
        TransactionKernel. It does not parse model output or execute tools
        directly.

    Complexity:
        O(e) time and memory across yielded events, where ``e`` is the number
        of stream events produced by TransactionKernel.
    """
    stream_run_id = resolve_stream_run_id(request.run_id, kernel.workspace)
    original_run_id = request.run_id
    if original_run_id is None and stream_run_id:
        request.run_id = stream_run_id
    logger.warning(
        "[run_stream] run_id resolved: original=%s stream_run_id=%s final=%s role=%s",
        original_run_id,
        stream_run_id,
        request.run_id,
        role,
    )
    inner_error: Exception | None = None
    uep_publisher = UEPEventPublisher()

    try:
        turn_setup = build_role_turn_prompt_setup(
            kernel=kernel,
            role=role,
            request=request,
        )
        profile = turn_setup.profile
        fingerprint = turn_setup.fingerprint
        system_prompt = turn_setup.system_prompt

        # Reset cached gateway for new turn (FailureBudget should not persist across turns).
        kernel._cached_tool_gateway = None
        kernel._cached_gateway_profile = None

        await uep_publisher.publish_stream_event(
            workspace=kernel.workspace or os.getcwd(),
            run_id=stream_run_id,
            role=role,
            event_type="fingerprint",
            payload={"fingerprint": str(fingerprint.full_hash or "")},
        )
        yield {"type": "fingerprint", "fingerprint": fingerprint}

        try:
            async for event in execute_transaction_kernel_stream(
                kernel,
                role=role,
                profile=profile,
                request=request,
                system_prompt=system_prompt,
                fingerprint=fingerprint,
                stream_run_id=stream_run_id,
                uep_publisher=uep_publisher,
            ):
                yield event
        except (RuntimeError, ValueError) as exc:
            inner_error = exc
            logger.exception("流式执行失败 (TransactionKernel)")
            await uep_publisher.publish_stream_event(
                workspace=kernel.workspace or os.getcwd(),
                run_id=stream_run_id,
                role=role,
                event_type="error",
                payload={"error": str(exc)},
            )
            yield {"type": "error", "error": str(exc)}

    except (RuntimeError, ValueError):
        if inner_error is None:
            raise


__all__ = ["execute_stream_role_turn"]
