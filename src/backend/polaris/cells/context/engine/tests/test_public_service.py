from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import patch

from polaris.cells.context.engine.public.contracts import (
    QueryFactoryRunContextSnapshotsV1,
    QueryFinalProviderRequestAuditV1,
)
from polaris.cells.context.engine.public.service import (
    build_context_window,
    get_anthropomorphic_context_v2,
    get_search_service,
    query_factory_run_context_snapshots,
    query_final_provider_request_audit,
)
from polaris.kernelone.context.engine import ContextBudget, ContextItem, ContextPack
from polaris.kernelone.llm.engine.context_store_retention import ContextSnapshotAuditPinRepository

if TYPE_CHECKING:
    import pytest


def test_query_factory_run_context_snapshots_uses_exact_durable_pin(tmp_path: Path) -> None:
    repository = ContextSnapshotAuditPinRepository(workspace=str(tmp_path))
    pin = repository.persist_snapshot_and_pin(
        snapshot={"schema_version": "llm.provider_request_snapshot.v2", "role": "pm"},
        factory_run_id="factory-exact",
        role="pm",
        verification_scope="factory",
        request_freeze_id="freeze-pm",
        provider_request_id="req-pm",
        composite_request_hash="a" * 64,
        snapshot_source="roles.kernel.final_physical_provider_request",
    )

    result = query_factory_run_context_snapshots(
        QueryFactoryRunContextSnapshotsV1(
            workspace=str(tmp_path),
            factory_run_id="factory-exact",
        )
    )

    assert result.ok is True
    assert result.status == "available"
    assert result.pins == (pin.to_record(),)


def test_query_final_physical_provider_request_projects_native_anthropic_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_ref = "a" * 24
    snapshot_path = tmp_path / context_ref
    native_body = {
        "model": "kimi-for-coding",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "build"}],
        "system": "polaris.role_identity.v1:director",
        "temperature": 0.0,
        "tools": [{"name": "write_file", "input_schema": {"type": "object"}}],
        "tool_choice": {"type": "auto"},
    }
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "llm.final_physical_provider_request_context.v1",
                "trace_id": "run-1",
                "call_id": "call-1",
                "messages": [{"role": "system", "content": "semantic"}],
                "provider_request": {
                    "role": "director",
                    "provider_id": "kimi",
                    "provider_type": "anthropic_compat",
                    "model": "kimi-for-coding",
                    "final_request_context_audit": {"audit_scope": "provider_native_wire"},
                    "final_physical_request": {
                        "endpoint": "https://api.kimi.com/coding/v1/messages",
                        "transport_kind": "http_post_json",
                        "body": native_body,
                    },
                    "physical_route_authority": {
                        "native_protocol": "anthropic_messages",
                        "native_request_schema_version": "llm.factory_provider_native_request.v1",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.service.context_snapshot_candidates",
        lambda _workspace, _context_hash: [("test", snapshot_path)],
    )

    result = query_final_provider_request_audit(
        QueryFinalProviderRequestAuditV1(workspace=str(tmp_path), context_snapshot_ref=context_ref)
    )

    assert result.ok is True
    assert result.payload["messages"] == [
        {"role": "system", "content": native_body["system"]},
        *native_body["messages"],
    ]
    assert result.payload["tools"] == native_body["tools"]
    assert result.payload["tool_choice"] == native_body["tool_choice"]
    assert result.payload["native_protocol"] == "anthropic_messages"
    assert result.payload["final_physical_request_body"] == native_body


def _query_physical_snapshot(
    *,
    tmp_path: Path,
    monkeypatch: Any,
    context_ref: str,
    native_protocol: str,
    native_body: dict[str, Any],
) -> Any:
    snapshot_path = tmp_path / context_ref
    snapshot_path.write_text(
        json.dumps(
            {
                "schema_version": "llm.final_physical_provider_request_context.v1",
                "trace_id": "run-1",
                "call_id": "call-1",
                "messages": [{"role": "system", "content": "semantic"}],
                "provider_request": {
                    "role": "director",
                    "provider_id": "openai",
                    "provider_type": "openai_compat",
                    "model": "gpt-test",
                    "final_request_context_audit": {"audit_scope": "provider_native_wire"},
                    "final_physical_request": {
                        "endpoint": "https://example.test/v1/provider",
                        "transport_kind": "http_post_json",
                        "body": native_body,
                    },
                    "physical_route_authority": {
                        "native_protocol": native_protocol,
                        "native_request_schema_version": "llm.factory_provider_native_request.v1",
                    },
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "polaris.cells.context.engine.public.service.context_snapshot_candidates",
        lambda _workspace, _context_hash: [("test", snapshot_path)],
    )
    return query_final_provider_request_audit(
        QueryFinalProviderRequestAuditV1(workspace=str(tmp_path), context_snapshot_ref=context_ref)
    )


def test_query_final_physical_provider_request_projects_openai_chat_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_body = {
        "model": "gpt-test",
        "messages": [{"role": "system", "content": "system"}, {"role": "user", "content": "build"}],
        "tools": [{"type": "function", "function": {"name": "write_file", "parameters": {}}}],
        "tool_choice": "auto",
        "response_format": {"type": "json_object"},
    }
    result = _query_physical_snapshot(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_ref="b" * 24,
        native_protocol="openai_chat_completions",
        native_body=native_body,
    )

    assert result.ok is True
    assert result.payload["messages"] == native_body["messages"]
    assert result.payload["tools"] == native_body["tools"]
    assert result.payload["response_format"] == native_body["response_format"]
    assert result.payload["native_protocol"] == "openai_chat_completions"


def test_query_final_physical_provider_request_projects_openai_responses_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    native_body = {
        "model": "gpt-test",
        "input": [{"role": "system", "content": "system"}, {"role": "user", "content": "build"}],
        "tools": [{"type": "function", "name": "write_file", "parameters": {}}],
        "tool_choice": "auto",
    }
    result = _query_physical_snapshot(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_ref="c" * 24,
        native_protocol="openai_responses",
        native_body=native_body,
    )

    assert result.ok is True
    assert result.payload["messages"] == native_body["input"]
    assert result.payload["tools"] == native_body["tools"]
    assert result.payload["native_protocol"] == "openai_responses"


def test_query_final_physical_provider_request_rejects_unknown_native_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _query_physical_snapshot(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        context_ref="d" * 24,
        native_protocol="opaque_unknown",
        native_body={"messages": [{"role": "user", "content": "build"}]},
    )

    assert result.ok is False
    assert result.status == "invalid_snapshot"
    assert result.error_code == "final_physical_provider_request_invalid"


def _base_pack() -> ContextPack:
    return ContextPack(
        request_hash="req_1",
        items=[
            ContextItem(
                kind="docs",
                provider="docs",
                content_or_pointer="Base context payload",
                size_est=8,
                priority=10,
                reason="base context",
            )
        ],
        compression_log=[],
        rendered_prompt="Base context payload",
        rendered_messages=[{"role": "user", "content": "Base context payload"}],
        total_tokens=8,
        total_chars=len("Base context payload"),
    )


def _context_override() -> dict[str, object]:
    return {
        "session_continuity": {
            "summary": "Older discussion preserved continuity for the restore flow.",
            "source_message_count": 4,
        },
        "state_first_context_os": {
            "adapter_id": "code",
            "run_card": {
                "current_goal": "Fix context.engine continuity overlay",
                "hard_constraints": [
                    "Keep roles.session as the raw truth owner.",
                ],
                "open_loops": ["Verify HTTP restore consumers stay canonical."],
                "active_entities": ["SessionContinuityEngine", "context.engine"],
                "active_artifacts": ["art_1"],
                "next_action_hint": "Expose the overlay through the public service.",
            },
            "context_slice_plan": {
                "plan_id": "plan_1",
                "budget_tokens": 2048,
                "roots": ["current_task"],
                "included": [],
                "excluded": [],
                "pressure_level": "soft",
            },
            "episode_cards": [{"episode_id": "ep_1"}],
        },
    }


class TestBuildContextWindowContextOSOverlay:
    def test_get_search_service_delegates_to_public_context_engine_boundary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_search_service = object()

        def _fake_get_search_service() -> object:
            return fake_search_service

        monkeypatch.setattr(
            "polaris.cells.context.engine.internal.search_gateway.get_search_service",
            _fake_get_search_service,
        )

        assert get_search_service() is fake_search_service

    def test_build_context_window_prepends_context_os_overlay(self) -> None:
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=_base_pack(),
        ):
            pack, _, budget, sources = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=_context_override(),
                session_id="sess_1",
            )

        assert isinstance(budget, ContextBudget)
        assert sources
        assert pack.items[0].provider == "context_os_overlay"
        assert "【State-First Context OS】" in pack.rendered_prompt
        assert "Current goal: Fix context.engine continuity overlay" in pack.rendered_prompt
        assert pack.rendered_messages[0]["content"] == pack.rendered_prompt
        assert any(
            entry.get("action") == "context_os_overlay"
            and entry.get("summary", {}).get("current_goal") == "Fix context.engine continuity overlay"
            for entry in pack.compression_log
        )

    def test_build_context_window_overlay_uses_policy_model_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _Assembler:
            def __init__(self, *, model_window: int, safety_margin: float) -> None:
                captured["model_window"] = model_window
                captured["safety_margin"] = safety_margin

            def add_continuity(self, *_args: Any, **_kwargs: Any) -> Any:
                return SimpleNamespace(content="【State-First Context OS】\nwindow-aware overlay")

        monkeypatch.setattr("polaris.kernelone.context.chunks.PromptChunkAssembler", _Assembler)
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=_base_pack(),
        ):
            build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                policy={"model_window_tokens": 16_384},
                context_override=_context_override(),
                session_id="sess_1",
            )

        assert captured["model_window"] == 16_384
        assert captured["safety_margin"] == 0.85

    def test_build_context_window_overlay_without_policy_uses_minimum_window(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, Any] = {}

        class _Assembler:
            def __init__(self, *, model_window: int, safety_margin: float) -> None:
                captured["model_window"] = model_window
                captured["safety_margin"] = safety_margin

            def add_continuity(self, *_args: Any, **_kwargs: Any) -> Any:
                return SimpleNamespace(content="【State-First Context OS】\nminimum-window overlay")

        monkeypatch.setattr("polaris.kernelone.context.chunks.PromptChunkAssembler", _Assembler)
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=_base_pack(),
        ):
            build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                policy={},
                context_override=_context_override(),
                session_id="sess_1",
            )

        assert captured["model_window"] == 1
        assert captured["safety_margin"] == 0.85

    def test_build_context_window_without_override_keeps_pack_unchanged(self) -> None:
        original = _base_pack()
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
            )

        assert pack.rendered_prompt == "Base context payload"
        assert pack.items[0].provider == "docs"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_can_disable_overlay_via_override_flag(self) -> None:
        original = _base_pack()
        override = _context_override()
        override["state_first_context_os_enabled"] = False
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=override,
            )

        assert pack.rendered_prompt == "Base context payload"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_can_disable_overlay_via_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = _base_pack()
        monkeypatch.setenv("KERNELONE_CONTEXT_OS_ENABLED", "off")
        with patch(
            "polaris.cells.context.engine.public.service._build_context_pack",
            return_value=original,
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                context_override=_context_override(),
            )

        assert pack.rendered_prompt == "Base context payload"
        assert not any(entry.get("action") == "context_os_overlay" for entry in pack.compression_log)

    def test_build_context_window_loads_session_override_when_session_id_present(self) -> None:
        class _FakeRoleSessionService:
            def __enter__(self) -> _FakeRoleSessionService:
                return self

            def __exit__(
                self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
            ) -> Literal[False]:
                return False

            def get_context_config_dict(self, session_id: str):
                return _context_override()

        with (
            patch(
                "polaris.cells.context.engine.public.service._build_context_pack",
                return_value=_base_pack(),
            ),
            patch(
                "polaris.cells.roles.session.public.RoleSessionService",
                _FakeRoleSessionService,
            ),
        ):
            pack, _, _, _ = build_context_window(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=3,
                run_id="run_1",
                mode="interactive",
                session_id="sess_implicit",
            )

        assert pack.items[0].provider == "context_os_overlay"
        assert "State-First Context OS" in pack.rendered_prompt


class TestAnthropomorphicContextV2ContextOSOverlay:
    def test_get_anthropomorphic_context_v2_returns_context_os_summary(self) -> None:
        with (
            patch(
                "polaris.cells.context.engine.public.service._build_context_pack",
                return_value=_base_pack(),
            ),
            patch(
                "polaris.cells.context.engine.public.service.init_anthropomorphic_modules",
                return_value=None,
            ),
            patch(
                "polaris.cells.context.engine.public.service.get_persona_text",
                return_value="Persona",
            ),
        ):
            payload = get_anthropomorphic_context_v2(
                project_root=".",
                role="director",
                query="continue fixing context engine",
                step=5,
                run_id="run_2",
                phase="execution",
                context_override=_context_override(),
                session_id="sess_2",
            )

        assert payload["persona_instruction"] == "Persona"
        assert "【Session Continuity】" in payload["anthropomorphic_context"]
        assert payload["context_os_summary"]["adapter_id"] == "code"
        assert payload["context_os_summary"]["current_goal"] == "Fix context.engine continuity overlay"
