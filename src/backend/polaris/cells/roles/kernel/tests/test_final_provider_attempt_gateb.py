from __future__ import annotations

import asyncio
import copy
import json
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Coroutine, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    QueryFactEventsV1,
    bootstrap_fact_stream_workspace,
    query_fact_events,
)
from polaris.cells.roles.kernel.internal.llm_caller import (
    final_provider_attempt_gate as gate_module,
    final_provider_attempt_qualification as qualification_module,
    invoker as invoker_module,
)
from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    FinalRequestEvidenceCoverageError,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_gate import (
    DurableFinalProviderAttemptSnapshotStore,
    FinalProviderAttemptGate,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_inflight import (
    ProviderAttemptDrainError,
    ProviderAttemptInFlightCoordinator,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_provider_attempt_lifecycle import (
    StrictProviderAttemptLifecycleStore,
)
from polaris.cells.roles.kernel.internal.llm_caller.final_request_metrics import (
    provider_native_request_metrics,
    validated_final_context_evidence,
)
from polaris.cells.roles.kernel.internal.llm_caller.response_types import PreparedLLMRequest
from polaris.cells.roles.kernel.internal.llm_caller.stream_engine import StreamEngine
from polaris.cells.roles.kernel.public.physical_attempt_control import (
    FACTORY_PHYSICAL_ATTEMPT_GRANT_VIEW_SCHEMA,
    FactoryPhysicalAttemptGrantViewV1,
)
from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as request_fact_test
from polaris.cells.roles.kernel.tests._physical_attempt_control_test_double import (
    FactoryPhysicalAttemptTestControlError as FactoryPhysicalAttemptControlError,
    FactoryPhysicalAttemptTestControlPort as FactoryPhysicalAttemptLiveControlPort,
)
from polaris.infrastructure.llm.providers import provider_helpers
from polaris.infrastructure.llm.providers.anthropic_provider import AnthropicProvider
from polaris.infrastructure.llm.providers.provider_helpers import (
    invoke_stream_with_retry,
    invoke_stream_with_retry_and_handler,
    invoke_with_retry,
)
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository
from polaris.kernelone.llm.engine.contracts import FrozenFinalProviderAttemptV1, bind_physical_provider_dispatch_port
from polaris.kernelone.llm.engine.executor import AIExecutor
from polaris.kernelone.llm.types import Usage


class _Response:
    def __init__(self, *, status_code: int = 200, text: str = "", headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.ok = status_code < 400
        self.text = text
        self.headers = headers or {}

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": "ok"}}]}


class ClientResponseError(RuntimeError):
    """Retry-shaped aiohttp response error for physical stream tests."""




async def test_async_stream_cleanup_system_exit_is_cancelled_and_preserves_both_errors(
    tmp_path: Path,
) -> None:
    _bootstrap(tmp_path)
    gate, lifecycle = _gate(tmp_path)
    events: list[str] = []

    class _ResponseContext:
        async def __aenter__(self) -> object:
            events.append("post_enter")
            return object()

        async def __aexit__(self, *_args: object) -> None:
            events.append("response_exit")
            raise SystemExit("cleanup aborted")

    async def _consume(_response: object) -> AsyncIterator[str]:
        if False:
            yield "unreachable"
        raise ValueError("consume failed")

    with pytest.raises(ValueError, match="consume failed"):
        async for _item in gate.dispatch_stream_async(
            wire_request=_wire_request(gate),
            open_stream=lambda _wire: _ResponseContext(),
            consume=_consume,
        ):
            pass

    assert events == ["post_enter", "response_exit"]
    terminal = tuple(item for item in lifecycle.query_strict() if item["event_type"] == "provider_attempt.terminal")
    assert len(terminal) == 1
    assert terminal[0]["payload"]["status"] == "cancelled"
    assert "ValueError: consume failed" in terminal[0]["payload"]["error"]
    assert "SystemExit: cleanup aborted" in terminal[0]["payload"]["error"]
