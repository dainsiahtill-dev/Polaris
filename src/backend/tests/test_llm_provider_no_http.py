"""Verify that llm.provider_runtime and llm.provider_config cells are free of
HTTP framework pollution.

These tests call the cell service layer directly (no HTTP client) and assert
that:
  - domain exceptions are raised rather than fastapi.HTTPException
  - no fastapi symbols appear in the internal source files
"""
from __future__ import annotations

import importlib
import inspect
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Static: ensure no fastapi import in cell internal modules
# ---------------------------------------------------------------------------

_PROVIDER_RUNTIME_INTERNALS = [
    "polaris.cells.llm.provider_runtime.internal.provider_actions",
    "polaris.cells.llm.provider_runtime.internal.runtime_invoke",
    "polaris.cells.llm.provider_runtime.internal.runtime_support",
    "polaris.cells.llm.provider_runtime.internal.providers",
    "polaris.cells.llm.provider_runtime.internal.gpu_detector",
]

_PROVIDER_CONFIG_INTERNALS = [
    "polaris.cells.llm.provider_config.internal.provider_context",
    "polaris.cells.llm.provider_config.internal.test_context",
    "polaris.cells.llm.provider_config.internal.settings_sync",
]


@pytest.mark.parametrize("module_name", _PROVIDER_RUNTIME_INTERNALS + _PROVIDER_CONFIG_INTERNALS)
def test_no_fastapi_import_in_internal(module_name: str) -> None:
    """No internal module may import fastapi."""
    mod = importlib.import_module(module_name)
    source = inspect.getsource(mod)
    assert "from fastapi" not in source, (
        f"{module_name} contains a 'from fastapi' import — HTTP framework must not leak into internal layers"
    )
    assert "import fastapi" not in source, (
        f"{module_name} contains an 'import fastapi' statement — HTTP framework must not leak into internal layers"
    )


# ---------------------------------------------------------------------------
# provider_runtime: run_provider_action raises domain exception for unknown type
# ---------------------------------------------------------------------------

def test_run_provider_action_raises_domain_exception_for_unknown_provider() -> None:
    """run_provider_action must raise UnsupportedProviderTypeError, not HTTPException."""
    from polaris.cells.llm.provider_runtime.internal.provider_actions import run_provider_action
    from polaris.cells.llm.provider_runtime.public.contracts import (
        LlmProviderRuntimeError,
        UnsupportedProviderTypeError,
    )

    with pytest.raises(UnsupportedProviderTypeError) as exc_info:
        run_provider_action(
            action="health",
            provider_type="__totally_unknown_provider__",
            provider_cfg={},
            api_key=None,
        )
    exc = exc_info.value
    assert isinstance(exc, LlmProviderRuntimeError)
    assert exc.code == "unsupported_provider_type"
    assert "__totally_unknown_provider__" in str(exc)


def test_run_provider_action_does_not_raise_http_exception() -> None:
    """Confirm HTTPException is never raised by the internal action function."""
    try:
        from fastapi import HTTPException
    except ImportError:
        pytest.skip("fastapi not installed")

    from polaris.cells.llm.provider_runtime.internal.provider_actions import run_provider_action

    with pytest.raises(Exception) as exc_info:
        run_provider_action(
            action="health",
            provider_type="__bad_provider__",
            provider_cfg={},
            api_key=None,
        )
    assert not isinstance(exc_info.value, HTTPException), (
        "run_provider_action must not raise HTTPException"
    )


# ---------------------------------------------------------------------------
# provider_runtime: execute_provider_action returns ProviderInvocationResultV1
# on domain exception (does NOT propagate)
# ---------------------------------------------------------------------------

def test_execute_provider_action_absorbs_domain_exception() -> None:
    """execute_provider_action wraps UnsupportedProviderTypeError into a result."""
    from polaris.cells.llm.provider_runtime.public.contracts import InvokeProviderActionCommandV1
    from polaris.cells.llm.provider_runtime.public.service import execute_provider_action

    cmd = InvokeProviderActionCommandV1(
        action="health",
        provider_type="__nonexistent__",
    )
    result = execute_provider_action(cmd)
    assert result.ok is False
    assert result.error_code == "unsupported_provider_type"
    assert result.status == "failed"


# ---------------------------------------------------------------------------
# provider_config: resolve_provider_request_context raises ProviderNotFoundError
# ---------------------------------------------------------------------------

def test_resolve_provider_request_context_raises_domain_exception_when_not_found() -> None:
    """resolve_provider_request_context raises ProviderNotFoundError for unknown provider_id."""
    from polaris.cells.llm.provider_config.internal.provider_context import (
        resolve_provider_request_context,
    )
    from polaris.cells.llm.provider_config.public.contracts import (
        LlmProviderConfigError,
        ProviderNotFoundError,
    )

    with patch(
        "polaris.cells.llm.provider_config.internal.provider_context.load_llm_config_port",
        return_value={"providers": {}},
    ), pytest.raises(ProviderNotFoundError) as exc_info:
        resolve_provider_request_context("/tmp/fake_ws", "", "nonexistent_provider", None, None)

    exc = exc_info.value
    assert isinstance(exc, LlmProviderConfigError)
    assert exc.code == "provider_not_found"
    assert "nonexistent_provider" in str(exc)


def test_resolve_provider_request_context_does_not_raise_http_exception() -> None:
    """Confirm HTTPException is never raised by provider_context internal."""
    try:
        from fastapi import HTTPException
    except ImportError:
        pytest.skip("fastapi not installed")

    from polaris.cells.llm.provider_config.internal.provider_context import (
        resolve_provider_request_context,
    )

    with patch(
        "polaris.cells.llm.provider_config.internal.provider_context.load_llm_config_port",
        return_value={"providers": {}},
    ), pytest.raises(Exception) as exc_info:
        resolve_provider_request_context("/tmp/fake_ws", "", "missing", None, None)

    assert not isinstance(exc_info.value, HTTPException), (
        "resolve_provider_request_context must not raise HTTPException"
    )


# ---------------------------------------------------------------------------
# provider_config: resolve_llm_test_execution_context raises domain exceptions
# ---------------------------------------------------------------------------

def test_resolve_llm_test_context_raises_validation_error_when_model_missing() -> None:
    """Direct config path with base_url but no model raises ProviderConfigValidationError."""
    from polaris.cells.llm.provider_config.internal.test_context import (
        resolve_llm_test_execution_context,
    )
    from polaris.cells.llm.provider_config.public.contracts import ProviderConfigValidationError

    with pytest.raises(ProviderConfigValidationError) as exc_info:
        resolve_llm_test_execution_context(
            "/tmp/fake_ws",
            "",
            {"role": "connectivity", "base_url": "http://120.24.117.59:11434"},
        )
    assert exc_info.value.code == "provider_config_validation_error"


def test_resolve_llm_test_context_raises_role_not_configured() -> None:
    """Role not in config raises RoleNotConfiguredError."""
    from polaris.cells.llm.provider_config.internal.test_context import (
        resolve_llm_test_execution_context,
    )
    from polaris.cells.llm.provider_config.public.contracts import RoleNotConfiguredError

    with patch(
        "polaris.cells.llm.provider_config.internal.test_context.load_llm_config_port",
        return_value={"roles": {}, "providers": {}},
    ), pytest.raises(RoleNotConfiguredError) as exc_info:
        resolve_llm_test_execution_context(
            "/tmp/fake_ws",
            "",
            {"role": "nonexistent_role"},
        )
    assert exc_info.value.code == "role_not_configured"
    assert "nonexistent_role" in str(exc_info.value)


def test_resolve_llm_test_context_does_not_raise_http_exception() -> None:
    """Confirm HTTPException is never raised by test_context internal."""
    try:
        from fastapi import HTTPException
    except ImportError:
        pytest.skip("fastapi not installed")

    from polaris.cells.llm.provider_config.internal.test_context import (
        resolve_llm_test_execution_context,
    )

    with patch(
        "polaris.cells.llm.provider_config.internal.test_context.load_llm_config_port",
        return_value={"roles": {}, "providers": {}},
    ), pytest.raises(Exception) as exc_info:
        resolve_llm_test_execution_context("/tmp/fake_ws", "", {"role": "bad_role"})

    assert not isinstance(exc_info.value, HTTPException), (
        "resolve_llm_test_execution_context must not raise HTTPException"
    )


# ---------------------------------------------------------------------------
# provider_config: resolve_provider_context_contract absorbs domain exception
# ---------------------------------------------------------------------------

def test_resolve_provider_context_contract_absorbs_domain_exception() -> None:
    """Public service method returns a failed ProviderConfigResultV1, not raises."""
    from polaris.cells.llm.provider_config.public.contracts import (
        ProviderNotFoundError,
        ResolveProviderContextCommandV1,
    )
    from polaris.cells.llm.provider_config.public.service import resolve_provider_context_contract

    mock_settings = MagicMock()
    mock_settings.ramdisk_root = ""
    mock_settings.workspace = "/tmp/fake_ws"

    with patch(
        "polaris.cells.llm.provider_config.internal.provider_context.resolve_provider_request_context",
        side_effect=ProviderNotFoundError("missing"),
    ):
        cmd = ResolveProviderContextCommandV1(workspace="/tmp/fake_ws", provider_id="missing")
        result = resolve_provider_context_contract(mock_settings, cmd)

    assert result.ok is False
    assert result.error_code == "provider_not_found"
