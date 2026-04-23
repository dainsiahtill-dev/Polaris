"""TransactionKernel - canonical entrypoint for transactional turn execution."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from polaris.cells.roles.kernel.internal.exploration_workflow import ExplorationWorkflowRuntime
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.cells.roles.kernel.public.turn_events import TurnEvent


class TransactionKernel(TurnTransactionController):
    """Thin canonical wrapper over TurnTransactionController.

    This class exists so callers can converge on a stable name without forcing an
    immediate file rename of the landed controller implementation.
    """

    def __init__(
        self,
        llm_provider: Callable,
        tool_runtime: Callable,
        config: TransactionConfig | None = None,
        workflow_runtime: ExplorationWorkflowRuntime | None = None,
        llm_provider_stream: Callable | None = None,
    ) -> None:
        super().__init__(
            llm_provider=llm_provider,
            tool_runtime=tool_runtime,
            config=config,
            workflow_runtime=workflow_runtime,
            llm_provider_stream=llm_provider_stream,
        )

    async def execute(self, turn_id: str, context: list[dict], tool_definitions: list[dict]) -> dict:
        return await super().execute(turn_id=turn_id, context=context, tool_definitions=tool_definitions)

    async def execute_stream(
        self,
        turn_id: str,
        context: list[dict],
        tool_definitions: list[dict],
        turn_request_id: str | None = None,
        parent_span_id: str | None = None,
    ) -> AsyncIterator[TurnEvent]:
        async for event in super().execute_stream(
            turn_id=turn_id,
            context=context,
            tool_definitions=tool_definitions,
            turn_request_id=turn_request_id,
            parent_span_id=parent_span_id,
        ):
            yield event
