"""Tests for log-pipeline run context ownership."""

from __future__ import annotations

from pathlib import Path

from polaris.infrastructure.log_pipeline.run_context import (
    RunContextManager,
    get_active_run_context,
    get_global_run_context,
    resolve_current_run_id,
    set_global_run_context,
)

BACKEND_ROOT = Path(__file__).resolve().parents[4]
RUN_CONTEXT_SOURCE = BACKEND_ROOT / "polaris" / "infrastructure" / "log_pipeline" / "run_context.py"


def test_process_wide_run_context_is_current_fallback(tmp_path: Path) -> None:
    """Non-thread-local writers use the explicit process-wide context fallback."""
    set_global_run_context(None)

    with RunContextManager(
        workspace=str(tmp_path),
        run_id="run-context-fallback",
        use_thread_local=False,
    ):
        assert get_active_run_context() is None
        global_context = get_global_run_context()
        assert global_context is not None
        assert global_context.run_id == "run-context-fallback"
        assert resolve_current_run_id() == "run-context-fallback"

    assert get_global_run_context() is None


def test_run_context_global_fallback_is_not_described_as_compatibility() -> None:
    """The process-wide fallback is active behavior, not an old architecture path."""
    source = RUN_CONTEXT_SOURCE.read_text(encoding="utf-8").lower()
    retired_phrase = "backward " + "compatibility"
    assert retired_phrase not in source
    assert "process-wide fallback context" in source

