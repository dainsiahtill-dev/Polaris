"""Unit tests for DirectorAdapter (director adapter internal methods)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from polaris.cells.roles.adapters.internal.director_adapter import DirectorAdapter

if TYPE_CHECKING:
    from pathlib import Path


def _make_director_adapter(workspace: Path) -> DirectorAdapter:
    """Create a DirectorAdapter for testing, defaulting to the given workspace."""
    return DirectorAdapter(workspace=str(workspace))


# ---------------------------------------------------------------------------
# BaseRoleAdapter methods — tested via DirectorAdapter instance
# ---------------------------------------------------------------------------


class TestBaseRoleAdapterMethods:
    """BaseRoleAdapter methods accessible via DirectorAdapter instance."""

    def test_build_env_default(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        env = adapter._build_env()
        assert env["PYTHONUTF8"] == "1"
        assert env["KERNELONE_WORKSPACE"] == str(tmp_path)

    def test_build_env_with_overrides(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        env = adapter._build_env({"MY_VAR": "my_value"})
        assert env["MY_VAR"] == "my_value"
        assert env["PYTHONUTF8"] == "1"  # still present

    def test_coerce_board_task_id_numeric(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        assert adapter._coerce_board_task_id("42") == 42
        assert adapter._coerce_board_task_id(99) == 99

    def test_coerce_board_task_id_task_prefix(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        assert adapter._coerce_board_task_id("task-7") == 7
        assert adapter._coerce_board_task_id("task-123-extra") == 123

    def test_coerce_board_task_id_empty(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        assert adapter._coerce_board_task_id("") is None
        assert adapter._coerce_board_task_id(None) is None
        assert adapter._coerce_board_task_id("  ") is None

    def test_coerce_board_task_id_non_numeric(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        assert adapter._coerce_board_task_id("abc") is None

    def test_next_task_trace_seq_increments(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        assert adapter._next_task_trace_seq("task-1") == 1
        assert adapter._next_task_trace_seq("task-1") == 2
        assert adapter._next_task_trace_seq("task-2") == 1  # separate counter

    def test_get_capabilities_includes_sequential(self, tmp_path) -> None:
        adapter = _make_director_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "sequential_execution" in caps


class TestResolveKernelRetryBudget:
    """_resolve_kernel_retry_budget: static — resolves max retries."""

    def test_default_is_1(self) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 1

    def test_respects_env_override(self, monkeypatch) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "3")
        result = BaseRoleAdapter._resolve_kernel_retry_budget("director")
        assert result == 3

    def test_clamped_to_0_3(self, monkeypatch) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "99")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 3
        monkeypatch.setenv("KERNELONE_DIRECTOR_KERNEL_MAX_RETRIES", "-1")
        assert BaseRoleAdapter._resolve_kernel_retry_budget("director") == 0


class TestResolveKernelValidationEnabled:
    """_resolve_kernel_validation_enabled: static — resolves validation flag."""

    def test_default_false(self) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is False

    def test_context_overrides(self) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", {"validate_output": True}) is True
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", {"validate_output": False}) is False

    def test_env_true(self, monkeypatch) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        monkeypatch.setenv("KERNELONE_DIRECTOR_VALIDATE_OUTPUT", "1")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is True

    def test_env_false(self, monkeypatch) -> None:
        from polaris.cells.roles.adapters.internal.base import BaseRoleAdapter

        monkeypatch.setenv("KERNELONE_DIRECTOR_VALIDATE_OUTPUT", "0")
        assert BaseRoleAdapter._resolve_kernel_validation_enabled("director", None) is False
