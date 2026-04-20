"""Test that TransactionKernel handoff contract uses canonical ContextHandoffPack.

This test file enforces ADR-0071:
- No second HandoffPack schema is created inside roles.kernel.
- Handoff/export/rehydrate aligns with factory.cognitive_runtime contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.cells.factory.cognitive_runtime.public.contracts import (
    ExportHandoffPackCommandV1,
    RehydrateHandoffPackCommandV1,
)
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.domain.cognitive_runtime.models import ContextHandoffPack, TurnEnvelope


@dataclass
class _MockProfile:
    role_id: str = "director"
    version: str = "1.0"
    model: str = "test-model"
    provider_id: str = "openai"
    tool_policy: Any = field(default_factory=lambda: MagicMock(policy_id="tp1", whitelist=["read_file"]))


@dataclass
class _MockFingerprint:
    full_hash: str = "abc123"


@dataclass
class _MockRequest:
    message: str = "hello"
    history: list[tuple[str, str]] = field(default_factory=list)
    max_retries: int = 0
    validate_output: bool = False
    task_id: str | None = None
    run_id: str | None = "run_123"
    workspace: str = "."
    prompt_appendix: str = ""
    system_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    context_override: dict[str, Any] | None = field(default_factory=lambda: {"context_os_snapshot": {}})
    tool_results: list[dict[str, Any]] = field(default_factory=list)


class TestCanonicalHandoffPackType:
    def test_no_private_handoff_schema_in_roles_kernel(self) -> None:
        """Assert that roles.kernel does not define its own HandoffPack dataclass/model."""
        import polaris.cells.roles.kernel

        # If anyone added a private HandoffPack, it would likely be in public.turn_contracts
        # or a dedicated handoff_pack module. We verify neither exists.
        from polaris.cells.roles.kernel.public import turn_contracts

        for attr in dir(turn_contracts):
            if "handoff" in attr.lower() and attr.lower() != "handoff_workflow":
                raise AssertionError(f"Unexpected private handoff schema found: {attr}")

        # Also verify no handoff_pack module exists
        import os

        kernel_dir = os.path.dirname(polaris.cells.roles.kernel.__file__)
        for root, _dirs, files in os.walk(kernel_dir):
            for f in files:
                if "handoff_pack" in f.lower() and f.endswith(".py"):
                    raise AssertionError(f"Private handoff_pack module found: {os.path.join(root, f)}")

    def test_build_context_handoff_pack_returns_domain_model(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        request = _MockRequest(run_id="run_123", task_id="task_456")

        turn_result = {
            "turn_id": "turn_789",
            "kind": "handoff_workflow",
            "visible_content": "handoff",
            "workflow_context": {
                "handoff_reason": "complex_exploration",
                "recoverable_context": {
                    "decision": {
                        "metadata": {
                            "current_goal": "explore codebase",
                            "run_card": {"priority": "high"},
                        }
                    },
                    "batch_receipts": [
                        {"batch_id": "batch_a"},
                        {"batch_id": "batch_b"},
                    ],
                },
            },
        }

        pack = kernel._build_context_handoff_pack(turn_result, "director", cast(Any, request))

        assert isinstance(pack, ContextHandoffPack)
        assert pack.reason == "complex_exploration"
        assert pack.current_goal == "explore codebase"
        assert pack.run_card == {"priority": "high"}
        assert pack.receipt_refs == ("batch_a", "batch_b")
        assert isinstance(pack.turn_envelope, TurnEnvelope)
        assert pack.turn_envelope.role == "director"


class TestHandoffPackRoundTrip:
    def test_handoff_pack_round_trip_via_domain_model(self) -> None:
        pack = ContextHandoffPack(
            handoff_id="handoff_001",
            workspace=".",
            created_at="1713250000",
            session_id="sess_001",
            run_id="run_001",
            reason="async_operation",
            current_goal="finish refactor",
            run_card={"step": 3},
            context_slice_plan={"strategy": "top_down"},
            decision_log=({"turn_id": "t1"},),
            receipt_refs=("r1", "r2"),
            turn_envelope=TurnEnvelope(
                turn_id="t1", session_id="sess_001", run_id="run_001", role="director", receipt_ids=("r1",)
            ),
        )

        serialized = pack.to_dict()
        restored = ContextHandoffPack.from_mapping(serialized)

        assert restored is not None
        assert restored.handoff_id == pack.handoff_id
        assert restored.workspace == pack.workspace
        assert restored.reason == pack.reason
        assert restored.receipt_refs == pack.receipt_refs
        assert restored.turn_envelope is not None
        assert restored.turn_envelope.receipt_ids == ("r1",)


class TestFactoryContractAlignment:
    def test_export_handoff_command_from_handoff_pack_dict(self) -> None:
        pack = ContextHandoffPack(
            handoff_id="handoff_002",
            workspace=".",
            created_at="1713250001",
            session_id="sess_002",
            run_id="run_002",
            reason="exploration",
            receipt_refs=("r3",),
            turn_envelope=TurnEnvelope(
                turn_id="t2", session_id="sess_002", run_id="run_002", role="architect", receipt_ids=("r3",)
            ),
        )

        assert pack.turn_envelope is not None
        cmd = ExportHandoffPackCommandV1(
            workspace=pack.workspace,
            session_id=pack.session_id,
            run_id=pack.run_id,
            reason=pack.reason,
            turn_envelope=pack.turn_envelope.to_dict(),
        )

        assert cmd.workspace == "."
        assert cmd.session_id == "sess_002"
        assert cmd.run_id == "run_002"
        assert cmd.reason == "exploration"

    def test_rehydrate_handoff_command_from_handoff_pack(self) -> None:
        pack = ContextHandoffPack(
            handoff_id="handoff_003",
            workspace=".",
            created_at="1713250002",
            session_id="sess_003",
            run_id="run_003",
            reason="resume",
        )

        cmd = RehydrateHandoffPackCommandV1(
            workspace=pack.workspace,
            handoff_id=pack.handoff_id,
            target_role="director",
            target_session_id=pack.session_id,
        )

        assert cmd.handoff_id == "handoff_003"
        assert cmd.target_role == "director"
        assert cmd.target_session_id == "sess_003"


class TestTransactionKernelHandoffIntegration:
    @pytest.mark.asyncio
    async def test_transaction_kernel_handoff_workflow_produces_canonical_pack(self) -> None:
        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        mock_tk_result = {
            "turn_id": "turn_handoff",
            "kind": "handoff_workflow",
            "visible_content": "[HANDOFF]",
            "workflow_context": {
                "handoff_reason": "complex_exploration",
                "recoverable_context": {
                    "decision": {
                        "metadata": {
                            "current_goal": "deep analysis",
                            "run_card": {"batch": 2},
                        }
                    },
                    "batch_receipts": [{"batch_id": "batch_x"}],
                },
            },
            "metrics": {"duration_ms": 50, "llm_calls": 1, "tool_calls": 0},
        }

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute=AsyncMock(return_value=mock_tk_result)),
            ),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(build_context=AsyncMock(return_value=MagicMock(messages=[]))),
            ),
        ):
            result = await kernel._execute_transaction_kernel_turn(
                role="director",
                profile=cast(Any, profile),
                request=cast(Any, request),
                system_prompt="sys",
                fingerprint=fingerprint,
                observer_run_id="run_123",
                response_schema=None,
            )

        assert result.metadata.get("transaction_kind") == "handoff_workflow"
        handoff_dict = result.metadata.get("handoff_pack")
        assert handoff_dict is not None

        handoff_pack = ContextHandoffPack.from_mapping(handoff_dict)
        assert handoff_pack is not None
        assert isinstance(handoff_pack, ContextHandoffPack)
        assert handoff_pack.reason == "complex_exploration"
        assert handoff_pack.current_goal == "deep analysis"
        assert handoff_pack.receipt_refs == ("batch_x",)
        assert handoff_pack.turn_envelope is not None
        assert handoff_pack.turn_envelope.role == "director"

    @pytest.mark.asyncio
    async def test_transaction_kernel_stream_handoff_emits_completion_event(self) -> None:
        """Stream path should also yield completion when handoff occurs."""
        from polaris.cells.roles.kernel.public.turn_events import CompletionEvent

        kernel = RoleExecutionKernel.create_default(workspace=".")
        profile = _MockProfile(role_id="director")
        request = _MockRequest(run_id="run_123")
        fingerprint = _MockFingerprint()

        async def _mock_execute_stream(*_args, **_kwargs):
            yield CompletionEvent(
                turn_id="turn_stream_handoff",
                status="handoff",
                duration_ms=30,
                llm_calls=1,
                tool_calls=0,
            )

        with (
            patch.object(
                kernel,
                "_create_transaction_kernel",
                return_value=MagicMock(execute_stream=_mock_execute_stream),
            ),
            patch(
                "polaris.cells.roles.kernel.public.service.RoleContextGateway",
                return_value=MagicMock(build_context=AsyncMock(return_value=MagicMock(messages=[]))),
            ),
        ):
            events = []
            async for event in kernel._execute_transaction_kernel_stream(
                role="director",
                profile=cast(Any, profile),
                request=cast(Any, request),
                system_prompt="sys",
                fingerprint=fingerprint,
                stream_run_id="run_123",
                uep_publisher=MagicMock(publish_stream_event=AsyncMock()),
            ):
                events.append(event)

        assert any(str(event.get("type")) == "complete" for event in events)
