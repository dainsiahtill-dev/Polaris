"""Public service exports for `workspace.integrity` cell.

Keep this module import-safe: optional heavy dependencies must not break
import-time contract usage in lightweight call-sites/tests.
All internal imports are lazy-loaded to maintain proper architectural boundaries.
"""

from __future__ import annotations

__all__ = [
    "CodeParser",
    "DirectorCodeIntelMixin",
    "FileChange",
    "FileChangeSnapshot",
    "FileChangeTracker",
    "TaskFileChangeTracker",
    "WorkspaceIntegrityService",
    "build_docs_templates",
    "clear_workspace_status",
    "create_workspace_integrity_service",
    "default_qa_commands",
    "detect_project_profile",
    "ensure_docs_ready_command",
    "ensure_docs_ready_or_raise",
    "generate_docs_templates_command",
    "get_abs_path",
    "get_code_parser",
    "is_safe_docs_path",
    "normalize_rel_path",
    "read_workspace_status",
    "select_docs_target_root",
    "validate_workspace",
    "validate_workspace_command",
    "workspace_has_docs",
    "workspace_status_path",
    "write_workspace_status",
]


class WorkspaceIntegrityService:
    """Contract-first facade for workspace integrity operations.

    This is a simplified public interface. The full implementation
    delegates to internal modules when needed.
    """

    def validate_workspace(self, command) -> str:
        from polaris.cells.workspace.integrity.internal.fs_utils import validate_workspace

        return validate_workspace(command.path, self_upgrade_mode=command.self_upgrade_mode)

    def ensure_docs_ready(self, command) -> None:
        from polaris.cells.workspace.integrity.internal.workspace_service import (
            ensure_docs_ready_or_raise,
        )

        ensure_docs_ready_or_raise(command.workspace)

    def generate_docs_templates(self, command):
        from polaris.cells.workspace.integrity.internal.workspace_service import (
            build_docs_templates,
            detect_project_profile,
        )

        files = build_docs_templates(
            workspace=command.workspace,
            mode=command.mode,
            fields=dict(command.fields),
            qa_commands=list(command.qa_commands),
        )
        return type(
            "DocsTemplatesResultV1",
            (),
            {
                "workspace": command.workspace,
                "mode": command.mode,
                "project_profile": detect_project_profile(command.workspace),
                "files": files,
            },
        )()


def create_workspace_integrity_service() -> WorkspaceIntegrityService:
    """Factory function: create a new WorkspaceIntegrityService instance.

    Callers (application layer, DI container) manage instance lifecycle.
    Tests can directly use ``WorkspaceIntegrityService()`` or this function
    without any global cleanup.

    Returns:
        WorkspaceIntegrityService: A new workspace integrity service instance.
    """
    return WorkspaceIntegrityService()


def validate_workspace_command(command) -> str:
    """Convenience wrapper: create instance on-demand, avoid module-level singleton."""
    return create_workspace_integrity_service().validate_workspace(command)


def ensure_docs_ready_command(command) -> None:
    """Convenience wrapper: create instance on-demand, avoid module-level singleton."""
    create_workspace_integrity_service().ensure_docs_ready(command)


def generate_docs_templates_command(command):
    """Convenience wrapper: create instance on-demand, avoid module-level singleton."""
    return create_workspace_integrity_service().generate_docs_templates(command)


def __getattr__(name: str):
    """Lazy import dispatcher for internal modules."""
    # Optional dependency - DirectorCodeIntelMixin
    if name == "DirectorCodeIntelMixin":
        try:
            # Lazy import to avoid cross-boundary import at module load time.
            from polaris.cells.workspace.integrity.internal.code_intel import (
                DirectorCodeIntelMixin as _dcim,
            )
        except (RuntimeError, ValueError):  # pragma: no cover - optional dependency path
            # Fallback mixin when code-intelligence stack is unavailable.
            class _dcim:  # type: ignore[no-redef]
                def __init__(self, *_args, **_kwargs) -> None:
                    pass

        globals()["DirectorCodeIntelMixin"] = _dcim
        return _dcim

    if name in {"CodeParser", "get_code_parser"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.code_parser import (
            CodeParser as _cp,
            get_code_parser as _gcp,
        )

        g = globals()
        g["CodeParser"] = _cp
        g["get_code_parser"] = _gcp
        return g[name]

    if name in {"FileChange", "FileChangeSnapshot", "FileChangeTracker", "TaskFileChangeTracker"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.diff_tracker import (
            FileChange as _fc,
            FileChangeSnapshot as _fcs,
            FileChangeTracker as _fct,
            TaskFileChangeTracker as _tfct,
        )

        g = globals()
        g["FileChange"] = _fc
        g["FileChangeSnapshot"] = _fcs
        g["FileChangeTracker"] = _fct
        g["TaskFileChangeTracker"] = _tfct
        return g[name]

    if name in {
        "get_abs_path",
        "normalize_rel_path",
        "validate_workspace",
        "workspace_has_docs",
        "workspace_status_path",
    }:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.fs_utils import (
            get_abs_path as _gap,
            normalize_rel_path as _nrp,
            validate_workspace as _vw,
            workspace_has_docs as _whd,
            workspace_status_path as _wsp,
        )

        g = globals()
        g["get_abs_path"] = _gap
        g["normalize_rel_path"] = _nrp
        g["validate_workspace"] = _vw
        g["workspace_has_docs"] = _whd
        g["workspace_status_path"] = _wsp
        return g[name]

    if name in {
        "build_docs_templates",
        "clear_workspace_status",
        "default_qa_commands",
        "detect_project_profile",
        "ensure_docs_ready_or_raise",
        "is_safe_docs_path",
        "read_workspace_status",
        "select_docs_target_root",
        "write_workspace_status",
    }:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.workspace_service import (
            build_docs_templates as _bdt,
            clear_workspace_status as _cws,
            default_qa_commands as _dqc,
            detect_project_profile as _dpp,
            ensure_docs_ready_or_raise as _edror,
            is_safe_docs_path as _isdp,
            read_workspace_status as _rws,
            select_docs_target_root as _sdtr,
            write_workspace_status as _wws,
        )

        g = globals()
        g["build_docs_templates"] = _bdt
        g["clear_workspace_status"] = _cws
        g["default_qa_commands"] = _dqc
        g["detect_project_profile"] = _dpp
        g["ensure_docs_ready_or_raise"] = _edror
        g["is_safe_docs_path"] = _isdp
        g["read_workspace_status"] = _rws
        g["select_docs_target_root"] = _sdtr
        g["write_workspace_status"] = _wws
        return g[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
