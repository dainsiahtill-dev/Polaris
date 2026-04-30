"""Test for runtime_config role binding mode.

This module tests the role binding mode resolution in the KernelOne
runtime configuration. The import has been migrated from app.llm to
polaris.kernelone.llm.runtime_config.
"""

import importlib.util

import pytest

if importlib.util.find_spec("polaris.kernelone.llm.runtime_config") is None:
    pytest.skip("Module not available: polaris.kernelone.llm.runtime_config", allow_module_level=True)

from polaris.kernelone.llm.runtime_config import _resolve_role_binding_mode


def test_default_role_binding_mode_is_strict(monkeypatch):
    monkeypatch.delenv("KERNELONE_ROLE_MODEL_BINDING_MODE", raising=False)
    assert _resolve_role_binding_mode() == "strict"
