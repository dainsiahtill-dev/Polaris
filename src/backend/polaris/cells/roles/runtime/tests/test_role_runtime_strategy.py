"""RoleRuntimeService strategy integration tests.

These tests live in the runtime cell (where RoleRuntimeService lives)
rather than in the kernelone/context cell to respect the import fence:
kernelone may NOT import from polaris/cells, but cells may import from kernelone.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn, cast

import pytest
from polaris.kernelone.context import ResolvedStrategy


class TestRoleRuntimeServiceStrategy:
    """RoleRuntimeService strategy integration tests (no I/O)."""

    def test_resolve_strategy_profile_returns_resolved(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        resolved = svc.resolve_strategy_profile(domain="code", role="director")
        assert isinstance(resolved, ResolvedStrategy)
        assert resolved.profile.profile_id == "canonical_balanced"

    def test_create_strategy_run_increments_turn(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        ctx1 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-test",
            budget=None,
            workspace="/repo",
        )
        ctx2 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-test",
            budget=None,
            workspace="/repo",
        )
        ctx3 = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-other",
            budget=None,
            workspace="/repo",
        )
        # Same session: turn index increments
        assert ctx2.turn_index == ctx1.turn_index + 1
        # Different session: starts from 0
        assert ctx3.turn_index == 0

    def test_create_strategy_run_with_session_override(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        # Without session_override: gets canonical
        ctx = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id=None,
            budget=None,
            workspace="/repo",
        )
        assert ctx.profile_id == "canonical_balanced"

    def test_create_strategy_run_applies_current_turn_cognitive_override(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        ctx = svc.create_strategy_run(
            domain="code",
            role="director",
            session_id="sess-cognitive",
            budget=None,
            workspace="/repo",
            current_turn_override={
                "compaction": {"trigger_at_budget_pct": 0.9},
                "cognitive_runtime": {
                    "source": "cognitive_runtime_mainline",
                    "applied": True,
                    "execution_path": "verify_then_write",
                },
            },
        )

        assert ctx.resolved_overrides["compaction"]["trigger_at_budget_pct"] == 0.9
        assert ctx.resolved_overrides["cognitive_runtime"]["applied"] is True
        assert ctx.resolved_overrides["cognitive_runtime"]["execution_path"] == "verify_then_write"

    def test_resolve_strategy_profile_prefers_domain_default_when_explicit(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        svc = RoleRuntimeService()
        role_default = svc.resolve_strategy_profile(domain="document", role="director")
        explicit_domain = svc.resolve_strategy_profile(
            domain="document",
            role="director",
            prefer_domain_default=True,
        )
        assert role_default.profile.profile_id == "canonical_balanced"
        assert explicit_domain.profile.profile_id == "speed_first"

    def test_resolve_strategy_auto_overlay_prefers_domain_target(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        resolved = RoleRuntimeService.resolve_strategy(domain="writing", role="director")
        assert resolved.profile.profile_id == "director.writer"

    def test_resolve_strategy_general_domain_falls_back_to_code_overlay(self) -> None:
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        resolved = RoleRuntimeService.resolve_strategy(domain="other", role="director")
        assert resolved.profile.profile_id == "director.execution"

    def test_build_session_request_propagates_context_domain(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="hello",
            context={"domain": "writing"},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_task_request_propagates_metadata_domain(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleTaskCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleTaskCommandV1(
            role="director",
            task_id="task-1",
            workspace="/repo",
            objective="do work",
            metadata={"domain": "analysis"},
        )
        request = RoleRuntimeService._build_task_request(command)
        assert request.domain == "research"
        assert request.metadata["domain"] == "research"

    def test_build_session_request_copies_provider_policy_to_context_override(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="sess-1",
            workspace="/repo",
            user_message="plan",
            metadata={
                "allowed_provider_types": ("ollama",),
                "llm_provider_policy": {"blocked_provider_types": ("openai_compat",)},
            },
        )

        request = RoleRuntimeService._build_session_request(command)

        assert request.context_override is not None
        assert request.context_override["allowed_provider_types"] == ("ollama",)
        assert request.context_override["llm_provider_policy"]["blocked_provider_types"] == ("openai_compat",)

    def test_build_task_request_copies_provider_policy_to_context_override(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleTaskCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleTaskCommandV1(
            role="director",
            task_id="task-1",
            workspace="/repo",
            objective="write code",
            metadata={"blocked_provider_types": ("openai_compat",)},
        )

        request = RoleRuntimeService._build_task_request(command)

        assert request.context_override is not None
        assert request.context_override["blocked_provider_types"] == ("openai_compat",)

    @pytest.mark.asyncio
    async def test_prepare_session_request_applies_cognitive_mainline_guidance(self, monkeypatch) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.kernelone.cognitive import middleware as cognitive_middleware

        class FakeCognitiveMiddleware:
            def __init__(self, *, workspace: str | None = None, enabled: bool | None = None) -> None:
                self.workspace = workspace
                self.enabled = enabled

            async def process(self, *, message: str, role_id: str, session_id: str | None = None):
                return {
                    "enabled": True,
                    "intent_type": "code_generation",
                    "confidence": 0.91,
                    "uncertainty_score": 0.17,
                    "execution_path": "verify_then_write",
                    "blocked": False,
                    "blocked_tools": ("delete_file", "run_command"),
                    "cognitive_analysis": {
                        "clarity_level": "high",
                        "verification_needed": True,
                        "actions_taken": ["inspect_scope", "write_tests"],
                    },
                }

        monkeypatch.setattr(cognitive_middleware, "CognitiveMiddleware", FakeCognitiveMiddleware)

        request = await RoleRuntimeService()._prepare_session_request(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id="sess-mainline",
                workspace="/repo",
                user_message="implement feature",
                metadata={"cognitive_runtime_mode": "mainline"},
                stream=False,
            )
        )

        assert request.context_override is not None
        assert request.context_override["cognitive_guidance"]["intent_type"] == "code_generation"
        assert request.context_override["cognitive_guidance"]["execution_path"] == "verify_then_write"
        assert request.context_override["cognitive_guidance"]["verification_needed"] is True
        assert request.context_override["cognitive_guidance"]["blocked_tools"] == ("delete_file", "run_command")
        assert request.metadata["cognitive_tool_policy"]["blocked_tools"] == ("delete_file", "run_command")
        assert request.metadata["cognitive_tool_policy"]["source"] == "cognitive_runtime_mainline"
        assert request.metadata["cognitive_runtime_preflight"]["mode"] == "mainline"
        assert request.metadata["cognitive_runtime_preflight"]["applied"] is True
        assert request.metadata["cognitive_runtime_preflight"]["tool_policy_applied"] is True
        assert request.metadata["cognitive_runtime_preflight"]["blocked_tools"] == ("delete_file", "run_command")
        assert request.metadata["cognitive_runtime_preflight"]["strategy_override_applied"] is True
        assert request.metadata["cognitive_strategy_override"]["cognitive_runtime"]["applied"] is True
        assert request.metadata["cognitive_strategy_override"]["exploration"]["max_expansion_depth"] == 4
        assert request.metadata["cognitive_strategy_override"]["compaction"]["trigger_at_budget_pct"] == 0.9

    @pytest.mark.asyncio
    async def test_prepare_session_request_fails_closed_when_cognitive_mainline_blocks(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.kernelone.cognitive import middleware as cognitive_middleware

        class BlockingCognitiveMiddleware:
            def __init__(self, *, workspace: str | None = None, enabled: bool | None = None) -> None:
                return None

            async def process(self, *, message: str, role_id: str, session_id: str | None = None):
                return {
                    "enabled": True,
                    "blocked": True,
                    "block_reason": "unsafe objective",
                }

        monkeypatch.setattr(cognitive_middleware, "CognitiveMiddleware", BlockingCognitiveMiddleware)

        with pytest.raises(RuntimeError, match="cognitive_runtime_blocked:unsafe objective"):
            await RoleRuntimeService()._prepare_session_request(
                ExecuteRoleSessionCommandV1(
                    role="director",
                    session_id="sess-blocked",
                    workspace="/repo",
                    user_message="do unsafe thing",
                    metadata={"cognitive_runtime_mode": "mainline"},
                    stream=False,
                )
            )

    @pytest.mark.asyncio
    async def test_create_transaction_controller_applies_cognitive_mainline_preflight(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.profile.public.service import RoleTurnRequest
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.kernelone.cognitive import middleware as cognitive_middleware

        captured: dict[str, object] = {}

        class FakeCognitiveMiddleware:
            def __init__(self, *, workspace: str | None = None, enabled: bool | None = None) -> None:
                self.workspace = workspace
                self.enabled = enabled

            async def process(self, *, message: str, role_id: str, session_id: str | None = None):
                return {
                    "enabled": True,
                    "intent_type": "code_generation",
                    "confidence": 0.87,
                    "uncertainty_score": 0.22,
                    "execution_path": "verify_then_write",
                    "blocked": False,
                    "blocked_tools": ("delete_file",),
                    "cognitive_analysis": {
                        "clarity_level": "high",
                        "verification_needed": True,
                        "actions_taken": ["inspect_scope"],
                    },
                }

        class FakeKernel:
            def _create_transaction_kernel(self, role, profile, request):
                captured["role"] = role
                captured["profile"] = profile
                captured["request"] = request
                return {"controller": True}

        monkeypatch.setattr(cognitive_middleware, "CognitiveMiddleware", FakeCognitiveMiddleware)
        service = RoleRuntimeService()
        monkeypatch.setattr(service, "_get_kernel", lambda _workspace: FakeKernel())

        controller = await service.create_transaction_controller(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id="sess-orchestrator-mainline",
                workspace="/repo",
                user_message="implement feature",
                metadata={"cognitive_runtime_mode": "mainline"},
                stream=True,
            )
        )

        request = cast(RoleTurnRequest, captured["request"])
        assert controller == {"controller": True}
        assert request.metadata["cognitive_runtime_preflight"]["applied"] is True
        assert request.metadata["cognitive_runtime_preflight"]["mode"] == "mainline"
        assert request.metadata["cognitive_tool_policy"]["blocked_tools"] == ("delete_file",)
        assert request.context_override is not None
        assert request.context_override["cognitive_guidance"]["execution_path"] == "verify_then_write"

    @pytest.mark.asyncio
    async def test_stream_chat_turn_applies_cognitive_strategy_before_fingerprint(self, monkeypatch) -> None:
        from polaris.cells.roles.profile.public.service import RoleTurnRequest, RoleTurnResult
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService
        from polaris.kernelone.cognitive import middleware as cognitive_middleware

        captured: dict[str, object] = {}

        class FakeCognitiveMiddleware:
            def __init__(self, *, workspace: str | None = None, enabled: bool | None = None) -> None:
                self.workspace = workspace
                self.enabled = enabled

            async def process(self, *, message: str, role_id: str, session_id: str | None = None):
                return {
                    "enabled": True,
                    "intent_type": "code_generation",
                    "confidence": 0.72,
                    "uncertainty_score": 0.58,
                    "execution_path": "verify_then_write",
                    "blocked": False,
                    "cognitive_analysis": {
                        "clarity_level": "medium",
                        "verification_needed": True,
                        "actions_taken": ["inspect_scope"],
                    },
                }

        class FakeKernel:
            async def run_stream(self, _role, request):
                captured["request"] = request
                yield {
                    "type": "complete",
                    "result": RoleTurnResult(content="done", turn_history=[("assistant", "done")]),
                }

        async def fake_persist(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(cognitive_middleware, "CognitiveMiddleware", FakeCognitiveMiddleware)
        monkeypatch.setattr(
            RoleRuntimeService, "emit_strategy_receipt", staticmethod(lambda *_args: Path("receipt.json"))
        )

        service = RoleRuntimeService()
        monkeypatch.setattr(service, "_get_kernel", lambda _workspace: FakeKernel())
        monkeypatch.setattr(service, "_persist_session_turn_state", fake_persist)

        events = [
            event
            async for event in service.stream_chat_turn(
                ExecuteRoleSessionCommandV1(
                    role="director",
                    session_id="sess-stream-mainline",
                    workspace="/repo",
                    user_message="implement feature",
                    domain="code",
                    metadata={"cognitive_runtime_mode": "mainline"},
                    stream=True,
                )
            )
        ]

        assert events[0]["type"] == "fingerprint"
        assert events[0]["cognitive_strategy_override_applied"] is True
        request = cast("RoleTurnRequest", captured["request"])
        assert request.metadata["cognitive_runtime_preflight"]["strategy_override_applied"] is True
        assert request.metadata["cognitive_strategy_override"]["cognitive_runtime"]["applied"] is True

    def test_required_cognitive_runtime_evidence_records_receipt_and_handoff(self, monkeypatch) -> None:
        from polaris.cells.factory.cognitive_runtime.public import service as cognitive_service
        from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
        from polaris.cells.roles.runtime.public.service import (
            RoleRuntimeService,
            _with_result_metadata_patch,
        )

        class FakeCognitiveService:
            def __init__(self) -> None:
                self.closed = False
                self.receipt_command = None
                self.handoff_command = None

            def record_runtime_receipt(self, command):
                self.receipt_command = command
                return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-1"))

            def export_handoff_pack(self, command):
                self.handoff_command = command
                return SimpleNamespace(ok=True, handoff=SimpleNamespace(handoff_id="handoff-1"))

            def close(self) -> None:
                self.closed = True

        fake_service = FakeCognitiveService()
        monkeypatch.setattr(
            cognitive_service,
            "get_cognitive_runtime_public_service",
            lambda: fake_service,
        )
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="director",
            workspace="/repo",
            task_id="task-1",
            session_id="session-1",
            run_id="run-1",
            output="done",
        )

        evidence = RoleRuntimeService()._emit_cognitive_runtime_shadow_artifacts(
            source="test",
            workspace="/repo",
            role="director",
            task_id="task-1",
            session_id="session-1",
            run_id="run-1",
            result=result,
            metadata={"cognitive_runtime_required": True},
            context={},
        )
        patched = _with_result_metadata_patch(result, {"cognitive_runtime_evidence": evidence})

        assert evidence["required"] is True
        assert evidence["receipt_recorded"] is True
        assert evidence["handoff_exported"] is True
        assert evidence["receipt_id"] == "receipt-1"
        assert evidence["handoff_id"] == "handoff-1"
        assert patched.metadata["cognitive_runtime_evidence"]["receipt_id"] == "receipt-1"
        assert fake_service.closed is True
        assert fake_service.receipt_command is not None
        assert fake_service.handoff_command is not None
        assert fake_service.receipt_command.payload["role"] == "director"
        assert fake_service.handoff_command.turn_envelope["receipt_ids"] == ["receipt-1"]

    def test_required_cognitive_runtime_evidence_fails_closed_when_disabled(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="/repo",
            session_id="session-1",
            output="done",
        )

        with pytest.raises(RuntimeError, match="cognitive_runtime_required_but_off"):
            RoleRuntimeService()._emit_cognitive_runtime_shadow_artifacts(
                source="test",
                workspace="/repo",
                role="pm",
                task_id=None,
                session_id="session-1",
                run_id=None,
                result=result,
                metadata={
                    "cognitive_runtime_required": True,
                    "cognitive_runtime_mode": "off",
                },
                context={},
            )

    def test_optional_cognitive_runtime_failure_is_observable_without_raising(self, monkeypatch) -> None:
        from polaris.cells.factory.cognitive_runtime.public import service as cognitive_service
        from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        class FailingCognitiveService:
            def record_runtime_receipt(self, _command):
                return SimpleNamespace(ok=False, error_message="receipt store unavailable")

            def close(self) -> None:
                return None

        monkeypatch.setattr(
            cognitive_service,
            "get_cognitive_runtime_public_service",
            lambda: FailingCognitiveService(),
        )
        result = RoleExecutionResultV1(
            ok=True,
            status="ok",
            role="pm",
            workspace="/repo",
            session_id="session-1",
            output="done",
        )

        evidence = RoleRuntimeService()._emit_cognitive_runtime_shadow_artifacts(
            source="test",
            workspace="/repo",
            role="pm",
            task_id=None,
            session_id="session-1",
            run_id=None,
            result=result,
            metadata={},
            context={},
        )

        assert evidence["required"] is False
        assert evidence["receipt_recorded"] is False
        assert evidence["error_message"] == "receipt store unavailable"

    @pytest.mark.asyncio
    async def test_execute_role_session_returns_cognitive_runtime_evidence_metadata(self, monkeypatch) -> None:
        from polaris.cells.factory.cognitive_runtime.public import service as cognitive_service
        from polaris.cells.roles.profile.public.service import RoleTurnResult
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        class FakeKernel:
            async def run(self, _role, _request):
                return RoleTurnResult(
                    content="done",
                    metadata={"turn_id": "turn-1"},
                    turn_history=[("assistant", "done")],
                )

        class FakeCognitiveService:
            def record_runtime_receipt(self, _command):
                return SimpleNamespace(ok=True, receipt=SimpleNamespace(receipt_id="receipt-2"))

            def export_handoff_pack(self, _command):
                return SimpleNamespace(ok=True, handoff=SimpleNamespace(handoff_id="handoff-2"))

            def close(self) -> None:
                return None

        async def fake_persist(*_args, **_kwargs) -> None:
            return None

        monkeypatch.setattr(
            cognitive_service,
            "get_cognitive_runtime_public_service",
            lambda: FakeCognitiveService(),
        )
        service = RoleRuntimeService()
        monkeypatch.setattr(service, "_get_kernel", lambda _workspace: FakeKernel())
        monkeypatch.setattr(service, "_persist_session_turn_state", fake_persist)

        result = await service.execute_role_session(
            ExecuteRoleSessionCommandV1(
                role="director",
                session_id="session-2",
                workspace="/repo",
                user_message="write",
                domain="code",
                metadata={"cognitive_runtime_required": True},
                stream=False,
            )
        )

        assert result.ok is True
        assert result.metadata["cognitive_runtime_evidence"]["required"] is True
        assert result.metadata["cognitive_runtime_evidence"]["receipt_id"] == "receipt-2"
        assert result.metadata["cognitive_runtime_evidence"]["handoff_id"] == "handoff-2"

    def test_build_session_request_injects_repo_intelligence_for_code_domain(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        class _FakeRepoMapResult:
            ranked_files = ["src/main.py"]
            ranked_symbols = ["main"]

            def to_text(self) -> str:
                return "【Ranked Files】\n  0.900 src/main.py"

        class _FakeFacade:
            def get_repo_map(self, **_kwargs):
                return _FakeRepoMapResult()

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            lambda **_kwargs: _FakeFacade(),
        )

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
            domain="code",
            context={"mentioned_idents": ["main"], "use_repo_intelligence": True},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.context_override is not None and "repo_intelligence" in request.context_override
        assert request.metadata.get("repo_intelligence_enabled") is True

    def test_build_session_request_skips_repo_intelligence_for_document_domain(
        self,
        monkeypatch,
    ) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        def _unexpected(**_kwargs) -> NoReturn:
            raise AssertionError("repo intelligence should not be called for document domain")

        monkeypatch.setattr(
            "polaris.kernelone.context.repo_intelligence.get_repo_intelligence",
            _unexpected,
        )

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="写文档",
            domain="document",
            context={"mentioned_idents": ["main"], "use_repo_intelligence": True},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.context_override is None or "repo_intelligence" not in request.context_override
        assert request.metadata.get("repo_intelligence_enabled") is None

    def test_build_session_request_defaults_to_document_for_pm(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_session_request_defaults_to_document_for_chief_engineer_alias(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="ChiefEnginner",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "document"
        assert request.metadata["domain"] == "document"

    def test_build_session_request_defaults_to_code_for_director(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="director",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "code"
        assert request.metadata["domain"] == "code"

    def test_build_session_request_explicit_domain_overrides_role_default(self) -> None:
        from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
        from polaris.cells.roles.runtime.public.service import RoleRuntimeService

        command = ExecuteRoleSessionCommandV1(
            role="pm",
            session_id="sess-1",
            workspace="/repo",
            user_message="继续",
            context={"domain": "code"},
        )
        request = RoleRuntimeService._build_session_request(command)
        assert request.domain == "code"
        assert request.metadata["domain"] == "code"
