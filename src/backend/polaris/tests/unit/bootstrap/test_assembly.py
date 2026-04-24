"""Tests for polaris.bootstrap.assembly module.

This module tests the application assembly and DI wiring functionality.
Given the module has complex cross-cell dependencies, we focus on testing
the extractable pure functions and logic.
"""

from __future__ import annotations


class TestAssemblyModuleImport:
    """Test that assembly module can be imported."""

    def test_import_assembly(self) -> None:
        """Should import the assembly module without errors."""
        from polaris.bootstrap import assembly

        assert hasattr(assembly, "assemble_core_services")

    def test_ensure_minimal_kernelone_bindings(self) -> None:
        """Should have the ensure_minimal_kernelone_bindings function."""
        from polaris.bootstrap import assembly

        assert hasattr(assembly, "ensure_minimal_kernelone_bindings")


class TestAssemblyPureLogic:
    """Test pure logic functions from assembly module."""

    def test_assemble_core_services_exists(self) -> None:
        """Should have the assemble_core_services function."""
        from polaris.bootstrap.assembly import assemble_core_services

        assert callable(assemble_core_services)

    def test_rebind_director_service_exists(self) -> None:
        """Should have the rebind_director_service function."""
        from polaris.bootstrap.assembly import rebind_director_service

        assert callable(rebind_director_service)
