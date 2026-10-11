"""Test for Phase 2: StateFirstContextOS.project() integration in RoleContextGateway.

This test verifies that RoleContextGateway.build_context() now calls
StateFirstContextOS.project() and uses the projection's active_window,
head_anchor, tail_anchor, and run_card instead of simple transcript expansion.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.roles.kernel.internal.context_gateway.projection_formatter import ProjectionFormatter
from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest


async def _prepare_ce_boundary(
    workspace: Path,
    *,
    prebuilt: bool = True,
    genuine: bool = True,
    decoy_role: str = "assistant",
    stream: bool = False,
    authority: Any = None,
    rows: list[dict[str, Any]] | None = None,
) -> tuple[Any, Any, dict[str, Any]]:
    """Exercise gateway projection and normal preparation; isolate ContextOS storage only."""
    from contextlib import nullcontext

    from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
    from polaris.cells.roles.kernel.internal.context_gateway.gateway_helpers import render_ce_command_planning_table
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
    from polaris.cells.roles.kernel.public.final_request_evidence_cutoff import bind_factory_role_evidence_authority
    from polaris.cells.roles.kernel.tests import test_role_turn_request_fact_projection as request_fact_test
    from polaris.kernelone.context.context_os import StateFirstContextOS
    from polaris.kernelone.context.context_os.models_v2 import TranscriptEventV2

    rows = (
        rows
        if rows is not None
        else [
            {
                "task_id": f"TASK-{i // 4 + 1}",
                "modality": "test",
                "argv": ["node", "--test", f"test-{i}.js"],
                "cwd": ".",
                "authority_hash": f"{i:064x}",
            }
            for i in range(28)
        ]
    )
    table = render_ce_command_planning_table(rows)
    profile = request_fact_test._profile("chief_engineer")
    profile.context_policy = SimpleNamespace(
        max_history_turns=8,
        max_context_tokens=262144,
        include_project_structure=False,
        include_task_history=False,
        compression_strategy="truncate",
    )
    profile.context_domain = "generic"
    context_override: dict[str, Any] = {
        "ordinary": "Prior user text.\n" + table,
        "nested": {"job_token_id": "private-test-token"},
        "_transaction_kernel_forced_tool_definitions": [],
        "_transaction_kernel_forced_tool_choice": "none",
        "pm_task_contract": {
            **request_fact_test._pm_contract_set(),
            "ce_required_command_authority": rows,
        },
    }
    if genuine:
        context_override["ce_required_command_authority"] = rows
    context = ContextRequest(
        message="Plan committed PM tasks.",
        history=[(decoy_role, table)],
        context_override=context_override,
    )
    projection = SimpleNamespace(
        head_anchor="",
        tail_anchor="",
        active_window=(
            TranscriptEventV2(
                event_id="history-1",
                sequence=1,
                role=decoy_role,
                kind="user_turn",
                route="clear",
                content=table,
            ),
        ),
        run_card=SimpleNamespace(
            current_goal="",
            open_loops=(),
            latest_user_intent="",
            pending_followup_action="",
            last_turn_outcome="",
        ),
        snapshot=None,
    )
    # No gateway/preparer/projection/serializer/audit method is replaced.
    # ContextOS persistence and model window discovery are external to this boundary.
    with (
        patch.object(StateFirstContextOS, "project", return_value=projection),
        patch.object(StateFirstContextOS, "resolved_context_window", new=262144),
        patch("polaris.cells.roles.kernel.internal.context_gateway.gateway.get_event_writer", return_value=MagicMock()),
    ):
        gateway = RoleContextGateway(profile, workspace=workspace)
        source_result = await gateway.build_context(context, system_prompt="You are Chief Engineer.")
        source_messages = [dict(item) for item in source_result.messages]
        if prebuilt:
            # TransactionKernel carries complete gateway message dictionaries.
            context_override["_transaction_kernel_prebuilt_messages"] = copy.deepcopy(source_messages)
            context_override["ce_required_command_authority"] = rows
        binding = bind_factory_role_evidence_authority(authority) if authority is not None else nullcontext()
        with binding:
            prepared = await LLMRequestPreparer(workspace=str(workspace))._prepare_llm_request(
                profile=profile,
                system_prompt="You are Chief Engineer.",
                context=context,
                temperature=0.2,
                max_tokens=60000,
                stream=stream,
                factory_semantic_identity=request_fact_test._semantic_identity() if authority is not None else None,
            )
    return (
        profile,
        prepared,
        {
            "source_messages": source_messages,
            "prepared_messages": copy.deepcopy(prepared.messages),
            "chat_messages": copy.deepcopy(prepared.ai_request.context["chat_messages"]),
            "context_result_messages": list(prepared.context_result.messages),
            "required_rows": rows,
        },
    )


@pytest.mark.parametrize("prebuilt", [False, True])
@pytest.mark.asyncio
async def test_ce_real_preparation_retains_source_but_strips_wire_names(tmp_path: Path, prebuilt: bool) -> None:
    """Catch source-name loss in both the gateway and TransactionKernel preparation paths."""
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
        build_final_request_context_audit_for_request,
    )
    from polaris.infrastructure.llm.providers.openai_provider import _build_openai_payload

    profile, prepared, capture = await _prepare_ce_boundary(tmp_path, prebuilt=prebuilt)
    chat_messages = prepared.ai_request.context["chat_messages"]
    assert chat_messages and all(set(item) == {"role", "content"} for item in chat_messages)
    wire = _build_openai_payload(
        prompt=prepared.input_text,
        model=profile.model,
        api_path="/v1/chat/completions",
        stream=False,
        config={**prepared.request_options, "chat_messages": chat_messages},
    )
    audit = build_final_request_context_audit_for_request(
        ai_request=prepared.ai_request, prepared=prepared, profile=profile
    )
    capture.update(serialized_body=wire, audit=audit)
    (tmp_path / "ce-real-boundary.json").write_text(json.dumps(capture, ensure_ascii=False, indent=2), encoding="utf-8")
    assert any(item.get("name") == "context_override" for item in prepared.messages)
    assert "private-test-token" not in json.dumps(wire)
    coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
    assert coverage["required"] is True
    assert coverage["pass"] is True
    assert coverage["included_rows"] == capture["required_rows"]
    assert wire["max_tokens"] == 60000


def test_ce_prebuilt_name_preservation_keeps_dedupe_and_empty_card_filter() -> None:
    from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer

    context = ContextRequest(
        message="Plan tasks.",
        context_override={
            "_transaction_kernel_prebuilt_messages": [
                {"role": "system", "name": "run_card", "content": "【Run Card】"},
                {"role": "system", "name": "context_override", "content": "Current planning context."},
                {"role": "user", "content": "Plan tasks."},
                {"role": "assistant", "name": "history", "content": "Previous response."},
                {"role": "user", "content": "Plan tasks."},
                {"role": "user", "content": "Plan tasks."},
            ],
        },
    )
    assert LLMRequestPreparer._extract_prebuilt_projection_messages(context) == [
        {"role": "system", "name": "context_override", "content": "Current planning context."},
        {"role": "assistant", "name": "history", "content": "Previous response."},
        {"role": "user", "content": "Plan tasks."},
    ]


@pytest.mark.parametrize("detect_injection", [False, True])
@pytest.mark.parametrize("merged", [False, True])
@pytest.mark.parametrize("genuine", [False, True])
def test_ce_projection_provenance_survives_real_system_merge(detect_injection, merged, genuine) -> None:
    """Ordinary context cannot impersonate the designated current PM table."""
    from types import SimpleNamespace

    from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
    from polaris.cells.roles.kernel.internal.context_gateway.gateway_helpers import render_ce_command_planning_table
    from polaris.cells.roles.kernel.internal.llm_caller.context_audit._payloads import _ce_required_command_row_coverage
    from polaris.kernelone.llm.engine.provider_native_request import build_openai_native_messages

    rows = [
        {
            "task_id": "TASK-3",
            "modality": "test",
            "argv": ["npx", "tsc", "--noEmit"],
            "cwd": ".",
            "authority_hash": "0f3aeaae01adf825871f0a18d328b7ffe6fbd84c343627035271c7c81339cfda",
        }
    ]
    projection_input = {
        "user_excerpt": "[UNTRUSTED_USER_MESSAGE]\nPrior conversation.\n" + render_ce_command_planning_table(rows),
        "ordinary": "Normal unrelated prose.",
    }
    if genuine:
        projection_input["ce_required_command_authority"] = rows
    projected = ContextOverrideProcessor(detect_prompt_injection=detect_injection).process_context_override(
        projection_input
    )
    assert projected is not None
    source = [
        {"role": "system", "content": "polaris.role_identity.v1:chief_engineer"},
        projected,
        {"role": "user", "content": "Plan current tasks."},
    ]
    wire = build_openai_native_messages(source) if merged else source
    request = SimpleNamespace(
        role="chief_engineer", context={"ce_required_command_authority": rows, "chat_messages": source}
    )
    coverage = _ce_required_command_row_coverage(request, wire)
    assert coverage["required"] is True
    assert coverage["pass"] is genuine
    assert coverage["included_rows"] == (rows if genuine else [])


def test_ce_required_table_is_complete_json_before_optional_context() -> None:
    import json

    from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor

    rows = [
        {
            "task_id": f"TASK-{i}",
            "modality": "test",
            "argv": ["node", "--test", f"dist/tests/{i}.js"],
            "cwd": ".",
            "authority_hash": f"{i:064x}",
        }
        for i in range(20)
    ]
    processor = ContextOverrideProcessor(detect_prompt_injection=True)
    result = processor.process_context_override(
        {
            "optional": "x" * 3000,
            "ce_required_command_authority": rows,
            "nested": {"job_token_id": "secret", "allowed_paths": ["secret"]},
        }
    )
    assert result is not None
    content = result["content"]
    assert content.startswith("[CE_REQUIRED_COMMAND_AUTHORITY_JSON]\n")
    table = json.loads(content.split("\n", 1)[1].split("\n[/CE_REQUIRED_COMMAND_AUTHORITY_JSON]", 1)[0])
    assert table == rows
    assert "secret" not in content


class TestStateFirstContextOSIntegration:
    """Test Phase 2: StateFirstContextOS.project() integration."""

    @pytest.mark.asyncio
    async def test_build_context_calls_state_first_project(self):
        """Verify build_context calls StateFirstContextOS.project()."""
        # Import here to avoid import errors if module has issues
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Mock the StateFirstContextOS.project() method
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Test head anchor"
        mock_projection.tail_anchor = "Test tail anchor"
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Test message",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = "Test goal"
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection) as mock_project:
            # Create request
            request = ContextRequest(
                message="Test message",
                history=[("user", "Hello")],
                context_os_snapshot=None,
            )

            # Build context
            result = await gateway.build_context(request)

            # Verify project() was called
            mock_project.assert_called_once()

            # Verify call arguments
            call_args = mock_project.call_args
            assert call_args is not None
            assert "messages" in call_args.kwargs or len(call_args.args) > 0
            assert "existing_snapshot" in call_args.kwargs or len(call_args.args) > 1
            assert "recent_window_messages" in call_args.kwargs

            # Verify messages were built from projection
            assert len(result.messages) > 0

            # Verify source includes state_first_context_os_initial_projection
            assert "state_first_context_os_initial_projection" in result.context_sources

    @pytest.mark.asyncio
    async def test_cognitive_strategy_override_expands_context_os_window(self):
        """MAINLINE cognitive strategy must affect ContextOS projection parameters."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 1000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        gateway = RoleContextGateway(mock_profile, workspace=".")

        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Test message",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = ""
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection) as mock_project:
            request = ContextRequest(
                message="Audit code generation deeply",
                history=[("user", "Hello")],
                context_os_snapshot=None,
                strategy_override={
                    "exploration": {
                        "max_expansion_depth": 4,
                        "neighbor_expansion_aggressive": True,
                    },
                    "compaction": {"trigger_at_budget_pct": 0.9},
                    "cognitive_runtime": {"applied": True},
                },
            )

            result = await gateway.build_context(request)

        mock_project.assert_called_once()
        call_args = mock_project.call_args
        assert call_args is not None
        assert call_args.kwargs["recent_window_messages"] == 16
        assert "cognitive_strategy_override" in result.context_sources
        assert result.metadata["strategy_override_applied"] is True
        assert result.metadata["strategy_override_sources"] == ["request.strategy_override"]
        assert result.metadata["recent_window_messages"] == 16
        assert result.metadata["base_recent_window_messages"] == 8
        assert result.metadata["context_budget_trigger_pct"] == 0.9
        assert result.metadata["effective_context_budget_tokens"] == 900

    def test_cognitive_strategy_override_is_control_plane_only(self):
        from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import (
            ContextOverrideProcessor,
        )

        message = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(
            {
                "cognitive_strategy_override": {"exploration": {"max_expansion_depth": 4}},
                "visible_business_context": "keep this",
            }
        )

        assert message is not None
        content = message["content"]
        assert "visible_business_context: keep this" in content
        assert "cognitive_strategy_override" not in content

    @pytest.mark.asyncio
    async def test_capability_profile_is_control_plane_projection_metadata(self):
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 1000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "provider-a"
        mock_profile.model = "qwen-16k"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        gateway = RoleContextGateway(mock_profile, workspace=".")

        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Test message",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = ""
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot
        capability_profile = {
            "schema_version": 1,
            "source": "roles.kernel.llm_caller.pre_projection",
            "role_id": "director",
            "provider_id": "provider-a",
            "model": "qwen-16k",
            "model_window_tokens": 16384,
            "supports_native_tools": True,
            "supports_json_schema": True,
        }

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="Test message",
                    history=[("user", "Hello")],
                    context_os_snapshot=None,
                    context_override={"metadata": {"capability_profile": capability_profile}},
                )
            )

        profile_ref = result.metadata["capability_profile_ref"]
        assert len(profile_ref["sha256"]) == 64
        assert profile_ref["source"] == "roles.kernel.llm_caller.pre_projection"
        assert profile_ref["role_id"] == "director"
        assert profile_ref["provider_id"] == "provider-a"
        assert profile_ref["model"] == "qwen-16k"
        assert profile_ref["model_window_tokens"] == 16384
        rendered_prompt = "\n".join(message["content"] for message in result.messages)
        assert "capability_profile" not in rendered_prompt

    @pytest.mark.asyncio
    async def test_build_context_with_snapshot_uses_projection(self):
        """Verify build_context uses projection when snapshot is provided."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
            WorkingStateV2 as WorkingState,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Create mock snapshot
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""
        mock_snapshot.transcript_log = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="Previous message",
            ),
        )
        mock_snapshot.working_state = WorkingState()
        mock_snapshot.artifact_store = ()
        mock_snapshot.pending_followup = None

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Head anchor from projection"
        mock_projection.tail_anchor = "Tail anchor from projection"
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_2",
                sequence=2,
                role="assistant",
                kind="assistant_turn",
                route="clear",
                content="Response from projection",
            ),
        )
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = ""
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection) as mock_project:
            # Create request with snapshot
            request = ContextRequest(
                message="New message",
                history=[("user", "New input")],
                context_os_snapshot={"version": 1, "transcript_log": []},
            )

            # Build context
            result = await gateway.build_context(request)

            # Verify project() was called
            mock_project.assert_called_once()

            # Verify source includes state_first_context_os_projection
            assert "state_first_context_os_projection" in result.context_sources

    @pytest.mark.asyncio
    async def test_budget_validation_error_triggers_emergency_truncate(self):
        """Verify BudgetPlan validation error triggers emergency truncation."""
        from polaris.cells.roles.kernel.internal.context_gateway import RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            BudgetPlanV2 as BudgetPlan,
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
            TranscriptEventV2 as TranscriptEvent,
        )

        # Create mock profile
        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 1000  # Small limit
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        # Create gateway
        gateway = RoleContextGateway(mock_profile, workspace=".")

        # Create mock snapshot with budget validation error
        budget_plan = BudgetPlan(
            model_context_window=1000,
            output_reserve=100,
            tool_reserve=50,
            safety_margin=50,
            input_budget=800,
            retrieval_budget=100,
            soft_limit=500,
            hard_limit=600,
            emergency_limit=700,
            current_input_tokens=500,
            expected_next_input_tokens=1200,
            p95_tool_result_tokens=200,
            planned_retrieval_tokens=100,
            validation_error="BudgetPlan invariant violated: expected_next_input_tokens exceeds model_context_window",
        )

        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = budget_plan
        mock_snapshot.transcript_log = ()
        mock_snapshot.artifact_store = ()
        mock_snapshot.pending_followup = None

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (
            TranscriptEvent(
                event_id="test_1",
                sequence=1,
                role="user",
                kind="user_turn",
                route="clear",
                content="A" * 1000,  # Large content to exceed budget
            ),
        )
        mock_projection.run_card = None
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            # Create request
            request = ContextRequest(
                message="Test",
                history=[("user", "Test")],
                context_os_snapshot={"version": 1},
            )

            # Build context - should apply emergency truncation
            result = await gateway.build_context(request)

            # Verify emergency truncation was applied
            assert "budget_violation_emergency_truncate" in result.context_sources or result.compression_applied

    @pytest.mark.asyncio
    async def test_director_context_includes_resident_agi_decision_trace(self, tmp_path):
        """Director final context should consume recent Resident/AGI decision guidance."""
        from polaris.cells.roles.kernel.internal.context_gateway import ContextGatewayConfig, RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        decision_trace = [
            {
                "actor": "resident",
                "stage": "goal_staging",
                "summary": "Promote governed Resident goal through PM bridge.",
                "strategy_tags": ["goal_governance", "pm_bridge"],
                "actual_outcome": {"decision_source": "resident_agi_supervisor"},
                "verdict": "success",
                "confidence": 0.86,
                "evidence_refs": ["workspace/meta/resident/decision_trace.jsonl"],
            }
        ]

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "director"
        mock_profile.display_name = "Director"

        gateway = RoleContextGateway(
            mock_profile,
            workspace=workspace,
            config=ContextGatewayConfig(
                resident_agi_decision_trace_provider=lambda _workspace: decision_trace,
            ),
        )
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""
        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = "Execute governed task"
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="Implement the next governed step",
                    history=[],
                    context_os_snapshot=None,
                )
            )

        assert "resident_agi_decision_trace" in result.context_sources
        assert any(
            "Resident AGI 决策交接" in str(message.get("content") or "")
            for message in result.messages
            if isinstance(message, dict)
        )

    @pytest.mark.asyncio
    async def test_resident_agi_context_includes_capability_decision_boundaries(self, tmp_path):
        """Resident AGI context must see capability surface plus hard/LLM decision boundaries."""
        from polaris.cells.roles.kernel.internal.context_gateway import ContextGatewayConfig, RoleContextGateway
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            ContextOSSnapshotV2 as ContextOSSnapshot,
        )

        workspace = tmp_path / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)

        mock_profile = MagicMock()
        mock_profile.context_policy = MagicMock()
        mock_profile.context_policy.max_history_turns = 8
        mock_profile.context_policy.max_context_tokens = 128000
        mock_profile.context_policy.include_project_structure = False
        mock_profile.context_policy.include_task_history = False
        mock_profile.context_policy.compression_strategy = "truncate"
        mock_profile.context_domain = None
        mock_profile.provider_id = "test_provider"
        mock_profile.model = "test_model"
        mock_profile.role_id = "resident_agi"
        mock_profile.display_name = "Resident AGI"

        capability_surface = {
            "schema_version": "resident.agi_capability_surface.v1",
            "decision_boundary_schema": "resident.agi_decision_boundary.v1",
            "role_id": "resident_agi",
            "runtime_foundation": "roles.runtime + ContextOS + TransactionKernel",
            "implementation_cell": "resident.autonomy",
            "items": [
                {
                    "capability_id": "contextos.final_request_audit.read",
                    "category": "llm_audit",
                    "access": "read_only",
                    "risk_level": "low",
                }
            ],
            "decision_boundaries": [
                {
                    "boundary_id": "platform_hard_rule",
                    "name": "Platform hard rule",
                    "authority": "platform",
                    "platform_hard_rule": "hard platform invariants are non-overridable",
                    "agi_decision_scope": "agi_decision_scope",
                }
            ],
        }
        gateway = RoleContextGateway(
            mock_profile,
            workspace=workspace,
            config=ContextGatewayConfig(
                resident_agi_capability_provider=lambda _workspace: capability_surface,
            ),
        )
        mock_snapshot = MagicMock(spec=ContextOSSnapshot)
        mock_snapshot.budget_plan = MagicMock()
        mock_snapshot.budget_plan.validation_error = ""
        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = "Supervise governed development"
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = mock_snapshot

        with patch.object(gateway._context_os, "project", return_value=mock_projection):
            result = await gateway.build_context(
                ContextRequest(
                    message="Decide the next unattended action",
                    history=[],
                    context_os_snapshot=None,
                )
            )

        rendered_prompt = "\n".join(
            str(message.get("content") or "") for message in result.messages if isinstance(message, dict)
        )
        capability_signal = gateway._signal_sources.get_resident_agi_capabilities()

        assert "resident_agi_capability_surface" in result.context_sources
        assert "Supervise governed development" in rendered_prompt
        assert capability_signal is not None
        assert "resident.agi_capability_surface.v1" in capability_signal
        assert "resident.agi_decision_boundary.v1" in capability_signal
        assert "platform_hard_rule" in capability_signal
        assert "agi_decision_scope" in capability_signal


class TestMessagesFromProjection:
    """Test projection-to-message formatting used by the gateway."""

    def test_creates_head_anchor_message(self):
        """Verify head_anchor creates a system message."""
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = "Test head"
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = None
        mock_projection.snapshot = None

        messages = ProjectionFormatter.messages_from_projection(mock_projection)

        # Should have head anchor message
        assert len(messages) >= 1
        head_msg = messages[0]
        assert head_msg["role"] == "system"
        assert head_msg["content"] == "Test head"
        assert head_msg["name"] == "context_head_anchor"

    def test_creates_active_window_messages(self):
        """Verify active_window events become messages."""
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            TranscriptEventV2 as TranscriptEvent,
        )

        event = TranscriptEvent(
            event_id="test_1",
            sequence=1,
            role="user",
            kind="user_turn",
            route="clear",
            content="Test content",
            _metadata={"key": "value"},
        )

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = (event,)
        mock_projection.run_card = None
        mock_projection.snapshot = None

        messages = ProjectionFormatter.messages_from_projection(mock_projection)

        # Should have active window message
        assert len(messages) >= 1
        active_msg = messages[0]
        assert active_msg["role"] == "user"
        assert active_msg["content"] == "Test content"
        assert "metadata" in active_msg

    def test_empty_run_card_is_omitted(self):
        """Empty run cards must not create system-message noise."""
        from polaris.kernelone.context.context_os.models_v2 import ContextOSProjectionV2 as ContextOSProjection

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = MagicMock()
        mock_projection.run_card.current_goal = ""
        mock_projection.run_card.open_loops = ()
        mock_projection.run_card.latest_user_intent = ""
        mock_projection.run_card.pending_followup_action = ""
        mock_projection.run_card.last_turn_outcome = ""
        mock_projection.snapshot = None

        messages = ProjectionFormatter.messages_from_projection(mock_projection)

        assert not [message for message in messages if message.get("name") == "run_card"]

    def test_creates_run_card_message(self):
        """Verify run_card creates a system message."""
        from polaris.kernelone.context.context_os.models_v2 import (
            ContextOSProjectionV2 as ContextOSProjection,
            RunCardV2 as RunCard,
        )

        run_card = RunCard(
            current_goal="Test goal",
            open_loops=("loop1", "loop2"),
            latest_user_intent="Test intent",
            pending_followup_action="Confirm",
            last_turn_outcome="affirm",
        )

        mock_projection = MagicMock(spec=ContextOSProjection)
        mock_projection.head_anchor = ""
        mock_projection.tail_anchor = ""
        mock_projection.active_window = ()
        mock_projection.run_card = run_card
        mock_projection.snapshot = None

        messages = ProjectionFormatter.messages_from_projection(mock_projection)

        # Should have run card message
        run_card_msgs = [m for m in messages if m.get("name") == "run_card"]
        assert len(run_card_msgs) == 1
        assert "Test goal" in run_card_msgs[0]["content"]
        assert "Open loops: 2" in run_card_msgs[0]["content"]


class TestEmergencyTruncatePreservesInstruction:
    """L2-10 regression: emergency_truncate dropped the FINAL user turn, the
    delivery contract defaulted to ANALYZE_ONLY, and the repair turn's
    write_file was silently filtered."""

    @staticmethod
    def _engine() -> object:
        from polaris.cells.roles.kernel.internal.context_gateway.compression_engine import (
            CompressionEngine,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
            TokenEstimator,
        )

        return CompressionEngine(
            max_context_tokens=128000,
            compression_strategy="recent",
            max_history_turns=20,
            token_estimator=TokenEstimator(),
            continuity_strategy=None,
            profile=None,
            workspace=None,
            reasoning_stripper=None,
        )

    def test_final_user_turn_survives_truncation(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "role prompt"},
            *[{"role": "assistant", "content": "x" * 400} for _ in range(30)],
            {"role": "user", "content": "实现 app.js 与 style.css 并落盘"},
        ]
        result = engine.emergency_truncate(messages, max_tokens=300)
        roles = [m["role"] for m in result]
        assert "user" in roles, f"final user turn must survive, got roles={roles}"
        assert result[-1]["role"] == "user"
        assert "app.js" in result[-1]["content"]

    def test_oversized_instruction_content_truncated_not_dropped(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "role prompt"},
            {"role": "user", "content": "修复任务: " + "y" * 50000},
        ]
        result = engine.emergency_truncate(messages, max_tokens=200)
        users = [m for m in result if m["role"] == "user"]
        assert len(users) == 1
        assert "EMERGENCY_TRUNCATED" in users[0]["content"]
        assert users[0]["content"].startswith("修复任务:")

    def test_history_still_dropped_first(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "old " * 200},
            {"role": "user", "content": "current instruction"},
        ]
        result = engine.emergency_truncate(messages, max_tokens=60)
        roles = [m["role"] for m in result]
        assert roles == ["system", "user"]

    def test_no_user_turn_behaves_as_before(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "s"},
            {"role": "assistant", "content": "a " * 500},
        ]
        result = engine.emergency_truncate(messages, max_tokens=50)
        assert all(m["role"] != "user" for m in result)


class TestEmergencyTruncateSystemPlanes:
    """L2-10 budget-crash root cause: PM planning's oversized "Current goal"
    system plane (5032 tok) was never compressed — the dialogue-only strategies
    are a no-op on system planes, so enforcement always raised before any write.
    emergency_truncate now trims the largest system planes as a last resort
    (compress, don't crash), after dropping history but BEFORE clipping the user
    instruction. The role_definition system prompt is injected post-enforcement,
    so it is never among these messages and cannot be clipped."""

    @staticmethod
    def _engine() -> object:
        from polaris.cells.roles.kernel.internal.context_gateway.compression_engine import (
            CompressionEngine,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
            TokenEstimator,
        )

        return CompressionEngine(
            max_context_tokens=128000,
            compression_strategy="recent",
            max_history_turns=20,
            token_estimator=TokenEstimator(),
            continuity_strategy=None,
            profile=None,
            workspace=None,
            reasoning_stripper=None,
        )

    @staticmethod
    def _estimate(messages: list) -> int:
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
            TokenEstimator,
        )

        return TokenEstimator().estimate(messages)

    def test_oversized_system_plane_truncated_to_fit(self):
        engine = self._engine()
        goal = "Current goal: " + ("需求条目 " * 4000)
        messages = [
            {"role": "system", "content": "pm_planning_pipeline backend_kind: generic"},
            {"role": "system", "content": goal, "name": "current_goal"},
            {"role": "system", "content": "Run Card: Goal short"},
            {"role": "user", "content": "选择 1-3 个任务推进 GLOBAL REQUIREMENTS"},
        ]
        result = engine.emergency_truncate(messages, max_tokens=1500)

        assert self._estimate(result) <= 1500, "assembled context must fit the budget, not raise"
        goal_planes = [m for m in result if m["role"] == "system" and m["content"].startswith("Current goal:")]
        assert len(goal_planes) == 1, "oversized goal plane is truncated, not dropped"
        assert "SYSTEM_PLANE_TRUNCATED" in goal_planes[0]["content"]
        assert goal_planes[0].get("name") == "current_goal", "plane name/metadata preserved"

    def test_user_instruction_preserved_when_system_plane_makes_room(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "Current goal: " + ("x" * 40000)},
            {"role": "user", "content": "实现 main.py 并落盘"},
        ]
        result = engine.emergency_truncate(messages, max_tokens=1200)

        users = [m for m in result if m["role"] == "user"]
        assert len(users) == 1
        # system-plane trimming runs BEFORE user truncation, so the instruction survives intact
        assert users[0]["content"] == "实现 main.py 并落盘"
        assert "EMERGENCY_TRUNCATED" not in users[0]["content"]
        assert self._estimate(result) <= 1200

    def test_largest_system_plane_trimmed_first(self):
        engine = self._engine()
        big = {"role": "system", "content": "BIG " + ("a" * 30000)}
        medium = {"role": "system", "content": "MED " + ("b" * 1200)}
        messages = [medium, big, {"role": "user", "content": "go"}]
        result = engine.emergency_truncate(messages, max_tokens=1200)

        big_planes = [m for m in result if m["content"].startswith("BIG")]
        med_planes = [m for m in result if m["content"].startswith("MED")]
        assert big_planes and "SYSTEM_PLANE_TRUNCATED" in big_planes[0]["content"]
        # the medium plane fits once the big one is trimmed, so it stays intact
        assert med_planes and "SYSTEM_PLANE_TRUNCATED" not in med_planes[0]["content"]
        assert self._estimate(result) <= 1200

    def test_small_system_planes_left_untouched(self):
        engine = self._engine()
        messages = [
            {"role": "system", "content": "tiny plane A"},
            {"role": "system", "content": "Current goal: " + ("z" * 40000)},
            {"role": "system", "content": "tiny plane B"},
            {"role": "user", "content": "do it"},
        ]
        result = engine.emergency_truncate(messages, max_tokens=1500)

        tinies = [m for m in result if m["content"].startswith("tiny plane")]
        assert len(tinies) == 2, "sub-floor system planes are never truncated"
        assert all("SYSTEM_PLANE_TRUNCATED" not in m["content"] for m in tinies)
        assert self._estimate(result) <= 1500
