from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm import agents_helpers  # noqa: E402


def test_build_fallback_returns_usable_agents_content() -> None:
    content = agents_helpers._build_fallback(
        docs_text="Project docs",
        root_text="Root readme",
        feedback_text="Please keep UTF-8",
        error_hint="no context",
    )
    assert "# AGENTS.md" in content
    assert "# AGENTS (Draft)" not in content
    assert "<INSTRUCTIONS>" in content
    assert "fallback_reason" in content


def test_is_failed_draft_detects_cli_error_output() -> None:
    assert agents_helpers._is_failed_draft("Error: Incorrect function. (os error 1)")


def test_is_usable_agents_content_requires_instruction_and_utf8() -> None:
    usable = """# AGENTS.md

<INSTRUCTIONS>
- Use UTF-8 explicitly for all text files.
- Follow PM -> ChiefEngineer -> Director flow.
</INSTRUCTIONS>
"""
    assert agents_helpers.is_usable_agents_content(usable, min_bytes=120)
    assert not agents_helpers.is_usable_agents_content("# AGENTS", min_bytes=120)


def test_gather_docs_context_reads_runtime_contract_plan(tmp_path: Path) -> None:
    """Test that gather_docs_context reads runtime contract files.

    The implementation uses KFS for path resolution with workspace-derived keys.
    This test verifies the function works with a properly set up workspace structure.
    """
    # Clear storage roots cache
    from polaris.kernelone.storage.layout import clear_storage_roots_cache
    clear_storage_roots_cache()

    # Create workspace structure with docs sentinel
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "docs").mkdir(exist_ok=True)

    # Create runtime contracts directory at the expected KFS location
    from polaris.kernelone.storage.layout import resolve_storage_roots, _resolve_storage_roots_impl

    # Create a custom resolver that uses the test workspace's local runtime instead of ramdisk
    def mock_storage_roots(workspace_path: str, ramdisk_root: str | None = None):
        roots = _resolve_storage_roots_impl(workspace_path, ramdisk_root=None)
        return type(roots)(
            workspace_abs=roots.workspace_abs,
            workspace_key=roots.workspace_key,
            storage_layout_mode=roots.storage_layout_mode,
            home_root=roots.home_root,
            global_root=roots.global_root,
            config_root=roots.config_root,
            projects_root=roots.projects_root,
            project_root=roots.project_root,
            project_persistent_root=roots.project_persistent_root,
            runtime_projects_root=roots.runtime_projects_root,
            # Use local workspace runtime directory instead of ramdisk
            runtime_project_root=str(workspace / ".polaris" / "runtime"),
            workspace_persistent_root=roots.workspace_persistent_root,
            runtime_base=roots.runtime_base,
            runtime_root=str(workspace / ".polaris" / "runtime"),
            runtime_mode="test_local_runtime",
            history_root=roots.history_root,
        )

    # Patch the internal resolver
    import polaris.kernelone.storage.layout as layout_module
    original_impl = layout_module._resolve_storage_roots_impl

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(layout_module, "_resolve_storage_roots_impl", mock_storage_roots)
        clear_storage_roots_cache()

        # Now the KFS will use our local runtime directory
        runtime_contracts = workspace / ".polaris" / "runtime" / "contracts"
        runtime_contracts.mkdir(parents=True, exist_ok=True)
        (runtime_contracts / "plan.md").write_text(
            "# Plan\n- Build Rust API\n",
            encoding="utf-8",
        )
        # Also create requirements.md as the implementation always tries to read it
        (runtime_contracts / "requirements.md").write_text(
            "# Requirements\n- Feature X\n",
            encoding="utf-8",
        )

        docs_text, root_text, docs_context = agents_helpers.gather_docs_context(
            str(workspace),
            "",  # cache_root=""
        )

    assert "Build Rust API" in docs_text
    assert "runtime/contracts/plan.md" in docs_context
    assert root_text == ""
