"""Public service exports for `workspace.integrity` cell.

Keep this module import-safe: optional heavy dependencies must not break
import-time contract usage in lightweight call-sites/tests.
All internal imports are lazy-loaded to maintain proper architectural boundaries.
"""

from __future__ import annotations

from typing import Any

# Pre-declare lazy-loaded names to satisfy __all__ and static analysis.
# These are populated via __getattr__ at access time.
# NOTE: Do NOT pre-declare names that are module-level definitions (e.g., WorkspaceIntegrityService).
# NOTE: Do NOT pre-declare names that are handled by __getattr__ with fallback classes,
#       because module-level None prevents __getattr__ from firing on import.
CodeParser: type | None = None
# DirectorCodeIntelMixin is NOT pre-declared here; it has a fallback class
# in __getattr__ and pre-declaring as None prevents __getattr__ from firing.
FileChange: type | None = None
FileChangeSnapshot: type | None = None
FileChangeTracker: type | None = None
TaskFileChangeTracker: type | None = None
build_docs_templates: type | None = None
clear_workspace_status: type | None = None
default_qa_commands: type | None = None
detect_project_profile: type | None = None
ensure_docs_ready_or_raise: type | None = None
get_abs_path: type | None = None
get_code_parser: type | None = None
is_safe_docs_path: type | None = None
normalize_rel_path: type | None = None
read_workspace_status: type | None = None
select_docs_target_root: type | None = None
validate_workspace: type | None = None
workspace_has_docs: type | None = None
workspace_status_path: type | None = None
write_workspace_status: type | None = None

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

    def generate_docs_templates(self, command) -> Any:
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


def generate_docs_templates_command(command) -> Any:
    """Convenience wrapper: create instance on-demand, avoid module-level singleton."""
    return create_workspace_integrity_service().generate_docs_templates(command)


def __getattr__(name: str) -> Any:
    """Lazy import dispatcher for internal modules."""
    # Optional dependency - DirectorCodeIntelMixin
    if name == "DirectorCodeIntelMixin":
        try:
            # Lazy import to avoid cross-boundary import at module load time.
            from polaris.cells.workspace.integrity.internal.code_intel import (
                DirectorCodeIntelMixin,
            )
        except (RuntimeError, ValueError):  # pragma: no cover - optional dependency path
            # Fallback mixin when code-intelligence stack is unavailable.

            class DirectorCodeIntelMixin:  # type: ignore[no-redef]
                def __init__(self, *_args, **_kwargs) -> None:
                    pass

        globals()["DirectorCodeIntelMixin"] = DirectorCodeIntelMixin
        return DirectorCodeIntelMixin

    if name in {"CodeParser", "get_code_parser"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.code_parser import (
            CodeParser,
            get_code_parser,
        )

        g = globals()
        g["CodeParser"] = CodeParser
        g["get_code_parser"] = get_code_parser
        return g[name]

    if name in {"FileChange", "FileChangeSnapshot", "FileChangeTracker", "TaskFileChangeTracker"}:
        # Lazy import to avoid cross-boundary import at module load time.
        from polaris.cells.workspace.integrity.internal.diff_tracker import (
            FileChange,
            FileChangeSnapshot,
            FileChangeTracker,
            TaskFileChangeTracker,
        )

        g = globals()
        g["FileChange"] = FileChange
        g["FileChangeSnapshot"] = FileChangeSnapshot
        g["FileChangeTracker"] = FileChangeTracker
        g["TaskFileChangeTracker"] = TaskFileChangeTracker
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
            get_abs_path,
            normalize_rel_path,
            validate_workspace,
            workspace_has_docs,
            workspace_status_path,
        )

        g = globals()
        g["get_abs_path"] = get_abs_path
        g["normalize_rel_path"] = normalize_rel_path
        g["validate_workspace"] = validate_workspace
        g["workspace_has_docs"] = workspace_has_docs
        g["workspace_status_path"] = workspace_status_path
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
            build_docs_templates,
            clear_workspace_status,
            default_qa_commands,
            detect_project_profile,
            ensure_docs_ready_or_raise,
            is_safe_docs_path,
            read_workspace_status,
            select_docs_target_root,
            write_workspace_status,
        )

        g = globals()
        g["build_docs_templates"] = build_docs_templates
        g["clear_workspace_status"] = clear_workspace_status
        g["default_qa_commands"] = default_qa_commands
        g["detect_project_profile"] = detect_project_profile
        g["ensure_docs_ready_or_raise"] = ensure_docs_ready_or_raise
        g["is_safe_docs_path"] = is_safe_docs_path
        g["read_workspace_status"] = read_workspace_status
        g["select_docs_target_root"] = select_docs_target_root
        g["write_workspace_status"] = write_workspace_status
        return g[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
