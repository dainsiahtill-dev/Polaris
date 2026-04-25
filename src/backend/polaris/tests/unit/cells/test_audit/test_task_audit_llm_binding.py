"""Tests for polaris.cells.audit.evidence.internal.task_audit_llm_binding.

Covers P0 data classes and P1 utility functions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from polaris.cells.audit.evidence.internal.task_audit_llm_binding import (
    AUDIT_COURT_DEPARTMENT,
    AUDIT_COURT_ROLE_ID,
    AUDIT_COURT_ROLE_NAME,
    AUDIT_TECH_ROLE_ID,
    AuditLLMBindingConfig,
    bind_audit_llm_to_task_service,
    build_audit_llm_binding_config,
    get_audit_role_descriptor,
    make_audit_llm_caller,
)


class TestAuditConstants:
    """Canonical audit role constants."""

    def test_tech_role_id_is_qa(self) -> None:
        assert AUDIT_TECH_ROLE_ID == "qa"

    def test_court_department_is_menxia(self) -> None:
        assert AUDIT_COURT_DEPARTMENT == "门下省"

    def test_court_role_id_is_menxia_shizhong(self) -> None:
        assert AUDIT_COURT_ROLE_ID == "menxia_shizhong"

    def test_court_role_name_is_menxia_shizhong(self) -> None:
        assert AUDIT_COURT_ROLE_NAME == "门下侍中"


class TestAuditLLMBindingConfig:
    """AuditLLMBindingConfig frozen dataclass."""

    def test_defaults(self) -> None:
        cfg = AuditLLMBindingConfig()
        assert cfg.enabled is True
        assert cfg.role_id == AUDIT_TECH_ROLE_ID
        assert cfg.timeout_seconds == 180
        assert cfg.prefer_local_ollama is True
        assert cfg.allow_remote_fallback is True
        assert cfg.fallback_model == ""

    def test_custom_values(self) -> None:
        cfg = AuditLLMBindingConfig(
            enabled=False,
            role_id="architect",
            timeout_seconds=60,
            prefer_local_ollama=False,
            allow_remote_fallback=False,
            fallback_model="gpt-4",
        )
        assert cfg.enabled is False
        assert cfg.role_id == "architect"
        assert cfg.timeout_seconds == 60


class TestGetAuditRoleDescriptor:
    """Canonical descriptor mapping."""

    def test_returns_expected_keys(self) -> None:
        desc = get_audit_role_descriptor()
        assert desc["tech_role_id"] == AUDIT_TECH_ROLE_ID
        assert desc["court_department"] == AUDIT_COURT_DEPARTMENT
        assert desc["court_role_id"] == AUDIT_COURT_ROLE_ID
        assert desc["court_role_name"] == AUDIT_COURT_ROLE_NAME


class TestBuildAuditLLMBindingConfig:
    """Config builder from settings-like object."""

    def test_all_defaults_from_empty_settings(self) -> None:
        class EmptySettings:
            audit_llm_role = None
            audit_llm_timeout = None
            model = None
            audit_llm_enabled = None
            audit_llm_prefer_local_ollama = None
            audit_llm_allow_remote_fallback = None

        settings: Any = EmptySettings()
        cfg = build_audit_llm_binding_config(settings)
        assert cfg.role_id == AUDIT_TECH_ROLE_ID
        assert cfg.timeout_seconds == 180
        assert cfg.enabled is True

    def test_custom_settings_applied(self) -> None:
        settings: Any = MagicMock()
        settings.audit_llm_role = "architect"
        settings.audit_llm_timeout = 120
        settings.model = "claude-3"
        settings.audit_llm_enabled = False
        settings.audit_llm_prefer_local_ollama = False
        settings.audit_llm_allow_remote_fallback = False

        cfg = build_audit_llm_binding_config(settings)
        assert cfg.role_id == "architect"
        assert cfg.timeout_seconds == 120
        assert cfg.fallback_model == "claude-3"
        assert cfg.enabled is False
        assert cfg.prefer_local_ollama is False
        assert cfg.allow_remote_fallback is False

    def test_timeout_clamped_to_minimum_30(self) -> None:
        class Settings:
            audit_llm_role = None
            audit_llm_timeout = 10
            model = None
            audit_llm_enabled = None
            audit_llm_prefer_local_ollama = None
            audit_llm_allow_remote_fallback = None

        cfg = build_audit_llm_binding_config(Settings())
        assert cfg.timeout_seconds == 30

    def test_invalid_timeout_falls_back_to_180(self) -> None:
        class Settings:
            audit_llm_role = None
            audit_llm_timeout = "not_a_number"
            model = None
            audit_llm_enabled = None
            audit_llm_prefer_local_ollama = None
            audit_llm_allow_remote_fallback = None

        cfg = build_audit_llm_binding_config(Settings())
        assert cfg.timeout_seconds == 180

    def test_empty_role_falls_back_to_default(self) -> None:
        class Settings:
            audit_llm_role = "   "
            audit_llm_timeout = None
            model = None
            audit_llm_enabled = None
            audit_llm_prefer_local_ollama = None
            audit_llm_allow_remote_fallback = None

        cfg = build_audit_llm_binding_config(Settings())
        assert cfg.role_id == AUDIT_TECH_ROLE_ID


class TestMakeAuditLLMCaller:
    """LLM caller factory behavior."""

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider")
    def test_no_llm_caller_returns_empty_when_provider_fails(self, mock_invoke: Any) -> None:
        mock_invoke.return_value = MagicMock(
            ok=False,
            output="",
            provider_id="",
            provider_type="",
            model="",
            attempted=False,
            latency_ms=0,
            error=None,
        )

        cfg = AuditLLMBindingConfig(prefer_local_ollama=False)
        caller = make_audit_llm_caller(workspace=".", settings=None, config=cfg)
        output, info = caller("qa", "prompt")
        assert output == ""
        assert info["llm_strategy"] == "role_runtime"
        assert info["tech_role_id"] == "qa"

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider")
    def test_local_ollama_success(self, mock_invoke: Any) -> None:
        mock_invoke.return_value = MagicMock(
            ok=True,
            output="audit result",
            provider_id="ollama-1",
            provider_type="ollama",
            model="llama3",
            attempted=True,
            latency_ms=150,
            error=None,
        )

        settings: Any = MagicMock()
        settings.workspace = "."
        cfg = AuditLLMBindingConfig(prefer_local_ollama=True, allow_remote_fallback=True)
        caller = make_audit_llm_caller(workspace=".", settings=settings, config=cfg)

        output, info = caller("qa", "prompt")
        assert output == "audit result"
        assert info["llm_strategy"] == "local_ollama"
        assert info["llm_provider_type"] == "ollama"

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider")
    def test_local_falls_back_to_remote_when_local_fails(self, mock_invoke: Any) -> None:
        # First call (local) fails, second call (remote) succeeds
        mock_invoke.side_effect = [
            MagicMock(
                ok=False,
                output="",
                provider_id="",
                provider_type="",
                model="",
                attempted=True,
                latency_ms=0,
                error="local unavailable",
            ),
            MagicMock(
                ok=True,
                output="remote result",
                provider_id="openai-1",
                provider_type="openai_compat",
                model="gpt-4",
                attempted=True,
                latency_ms=200,
                error=None,
            ),
        ]

        settings: Any = MagicMock()
        settings.workspace = "."
        cfg = AuditLLMBindingConfig(prefer_local_ollama=True, allow_remote_fallback=True)
        caller = make_audit_llm_caller(workspace=".", settings=settings, config=cfg)

        output, info = caller("qa", "prompt")
        assert output == "remote result"
        assert info["llm_strategy"] == "role_runtime_fallback"

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider")
    def test_no_fallback_when_not_allowed(self, mock_invoke: Any) -> None:
        mock_invoke.return_value = MagicMock(
            ok=False,
            output="",
            provider_id="",
            provider_type="",
            model="",
            attempted=True,
            latency_ms=0,
            error="local unavailable",
        )

        settings: Any = MagicMock()
        settings.workspace = "."
        cfg = AuditLLMBindingConfig(prefer_local_ollama=True, allow_remote_fallback=False)
        caller = make_audit_llm_caller(workspace=".", settings=settings, config=cfg)

        output, info = caller("qa", "prompt")
        assert output == ""
        assert info["llm_strategy"] == "local_ollama_only"

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.invoke_role_runtime_provider")
    def test_role_runtime_direct_when_no_local_preference(self, mock_invoke: Any) -> None:
        mock_invoke.return_value = MagicMock(
            ok=True,
            output="direct result",
            provider_id="anthropic-1",
            provider_type="anthropic_compat",
            model="claude-3",
            attempted=True,
            latency_ms=100,
            error=None,
        )

        settings: Any = MagicMock()
        settings.workspace = "."
        cfg = AuditLLMBindingConfig(prefer_local_ollama=False)
        caller = make_audit_llm_caller(workspace=".", settings=settings, config=cfg)

        output, info = caller("qa", "prompt")
        assert output == "direct result"
        assert info["llm_strategy"] == "role_runtime"


class TestBindAuditLLMToTaskService:
    """Side-effect binding to task service."""

    def test_disabled_returns_false(self) -> None:
        settings: Any = MagicMock()
        settings.audit_llm_enabled = False
        task_service: Any = MagicMock()
        assert bind_audit_llm_to_task_service(task_service=task_service, settings=settings, workspace=".") is False
        task_service.set_audit_llm_caller.assert_not_called()

    @patch("polaris.cells.audit.evidence.internal.task_audit_llm_binding.make_audit_llm_caller")
    def test_enabled_sets_caller(self, mock_make_caller: Any) -> None:
        settings: Any = MagicMock()
        settings.audit_llm_enabled = True
        task_service: Any = MagicMock()
        mock_caller = MagicMock()
        mock_make_caller.return_value = mock_caller

        assert bind_audit_llm_to_task_service(task_service=task_service, settings=settings, workspace=".") is True
        task_service.set_audit_llm_caller.assert_called_once_with(mock_caller)
