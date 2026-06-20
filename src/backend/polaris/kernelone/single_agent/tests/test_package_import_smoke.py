"""Import smoke tests for the KernelOne single-agent package.

UTF-8: all text literals in this file use UTF-8 encoding.

Guards the public surface that survived the removal of the dead
``runtime`` and ``tools`` islands: importing the package and its live
members must keep working, and the removed islands must stay gone.
"""

from __future__ import annotations

import importlib

import pytest


class TestSingleAgentPackageImport:
    """The single-agent package and its live members import cleanly."""

    def test_package_imports(self) -> None:
        """Importing the top-level package succeeds."""
        module = importlib.import_module("polaris.kernelone.single_agent")

        assert module.__name__ == "polaris.kernelone.single_agent"

    def test_role_framework_imports(self) -> None:
        """The live ``role_framework`` member imports without runtime/tools."""
        module = importlib.import_module("polaris.kernelone.single_agent.role_framework")

        assert module is not None

    def test_skill_system_imports(self) -> None:
        """The live ``skill_system`` member imports without runtime/tools."""
        module = importlib.import_module("polaris.kernelone.single_agent.skill_system")

        assert hasattr(module, "SkillLoader")


class TestDeadIslandsRemoved:
    """The dead ``runtime`` and ``tools`` islands are no longer importable."""

    @pytest.mark.parametrize(
        "dead_module",
        [
            "polaris.kernelone.single_agent.runtime",
            "polaris.kernelone.single_agent.tools",
        ],
    )
    def test_dead_module_is_gone(self, dead_module: str) -> None:
        """Removed islands raise ModuleNotFoundError on import."""
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(dead_module)
