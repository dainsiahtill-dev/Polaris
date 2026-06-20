"""Target-file + scope resolution collaborator for the Director worker.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 5). ``TargetFileResolver`` turns a PM task's ``target_files`` / ``file_plan``
/ description / declared scopes into a concrete, bounded list of workspace files
for the Director prompt.

The ``os.walk`` traversal ordering, the ``_SCOPE_INFERENCE_MAX_FILES`` /
``_SCOPE_INFERENCE_MAX_FILES_PER_SCOPE`` caps, and the source-extension
precedence MUST stay byte-identical to the original implementation; the bodies
below are moved verbatim.

This module depends only on the standard library + the pure ``path_predicates``
module + the ``WorkspaceProbe`` collaborator + domain (``Task``). It MUST NOT
import ``code_generation_engine`` / ``file_apply_service`` at module top.

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import os

from polaris.cells.director.tasking.internal import path_predicates
from polaris.cells.director.tasking.internal.workspace_probe import WorkspaceProbe
from polaris.domain.entities import Task

_SCOPE_INFERENCE_MAX_FILES = 12
_SCOPE_INFERENCE_MAX_FILES_PER_SCOPE = 3
_SCOPE_INFERENCE_SOURCE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".sql",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
)
_SCOPE_INFERENCE_IGNORED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".polaris",
    ".pytest_cache",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "runtime",
}


class TargetFileResolver:
    """Resolve concrete Director target files from a PM task contract."""

    def __init__(self, workspace: str, probe: WorkspaceProbe) -> None:
        self.workspace = workspace
        self._probe = probe

    def normalize_target_files(self, task: Task) -> list[str]:
        """Get normalized list of target files from task."""
        files: list[str] = []
        seen: set[str] = set()

        # Get from metadata
        metadata = task.metadata if isinstance(task.metadata, dict) else {}

        # From target_files
        target_files = metadata.get("target_files", [])
        if isinstance(target_files, list):
            for f in target_files:
                path = str(f or "").strip()
                if path and path_predicates.is_concrete_target_file_path(path) and path not in seen:
                    seen.add(path)
                    files.append(path)

        # From file_plan
        file_plan = metadata.get("file_plan", [])
        if isinstance(file_plan, list):
            for item in file_plan:
                if isinstance(item, dict):
                    path = str(item.get("path") or "").strip()
                    if path and path_predicates.is_concrete_target_file_path(path) and path not in seen:
                        seen.add(path)
                        files.append(path)

        # Fallback: extract from description
        if not files and task.description:
            for line in task.description.split("\n"):
                description_path = path_predicates.extract_description_target_path(line)
                if description_path and description_path not in seen:
                    seen.add(description_path)
                    files.append(description_path)

        if not files:
            for inferred_path in self.infer_target_files_from_scope_paths(task):
                if inferred_path not in seen:
                    seen.add(inferred_path)
                    files.append(inferred_path)

        return files

    def infer_target_files_from_scope_paths(self, task: Task) -> list[str]:
        """Infer concrete target files from PM directory scopes.

        PM contracts sometimes describe module scopes without concrete files.
        Passing that ambiguity through to the LLM creates broad prompts that can
        consume the whole task budget. This inference keeps the Director prompt
        bounded by existing files in the declared scopes and one conservative
        new file candidate for empty scopes.
        """
        inferred: list[str] = []
        seen: set[str] = set()
        for scope in self.normalize_scope_paths(task):
            if len(inferred) >= _SCOPE_INFERENCE_MAX_FILES:
                break
            scope_files = self.existing_files_for_scope(scope, task)
            if not scope_files:
                synthesized = self.synthesize_scope_target_file(scope, task)
                scope_files = [synthesized] if synthesized else []
            for path in scope_files:
                if len(inferred) >= _SCOPE_INFERENCE_MAX_FILES:
                    break
                if path and path not in seen:
                    seen.add(path)
                    inferred.append(path)
        source_targets = [path for path in inferred if not path_predicates.is_test_like_target_file(path)]
        if source_targets:
            return source_targets
        return inferred

    def existing_files_for_scope(self, scope: str, task: Task) -> list[str]:
        """Return existing workspace files under a declared scope."""
        normalized_scope = str(scope or "").strip().replace("\\", "/").rstrip("/")
        if not normalized_scope or os.path.isabs(normalized_scope) or ".." in normalized_scope.split("/"):
            return []
        if path_predicates.is_concrete_target_file_path(normalized_scope):
            full_file = self._probe.resolve_workspace_file_path(normalized_scope)
            if full_file and os.path.isfile(full_file):
                return [normalized_scope]
            return []

        full_scope = self._probe.resolve_workspace_file_path(normalized_scope)
        if not full_scope or not os.path.isdir(full_scope):
            return []

        candidates: list[str] = []
        for root, dirnames, filenames in os.walk(full_scope):
            dirnames[:] = [
                name
                for name in sorted(dirnames)
                if name not in _SCOPE_INFERENCE_IGNORED_DIRS and not name.startswith(".")
            ]
            for filename in sorted(filenames):
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.workspace).replace("\\", "/")
                if self.is_scope_inference_candidate(rel_path):
                    candidates.append(rel_path)
            if len(candidates) >= _SCOPE_INFERENCE_MAX_FILES_PER_SCOPE * 4:
                break

        tokens = path_predicates.task_ascii_tokens(task)
        candidates.sort(key=lambda path: path_predicates.scope_candidate_sort_key(path, tokens))
        return candidates[:_SCOPE_INFERENCE_MAX_FILES_PER_SCOPE]

    def is_scope_inference_candidate(self, path: str) -> bool:
        """Return whether an existing file is useful as an inferred target."""
        normalized = str(path or "").strip().replace("\\", "/")
        if not path_predicates.is_concrete_target_file_path(normalized):
            return False
        leaf = os.path.basename(normalized)
        if leaf.startswith(".") and leaf not in {".env", ".env.example", ".gitignore"}:
            return False
        lowered = normalized.lower()
        return lowered.endswith(_SCOPE_INFERENCE_SOURCE_EXTENSIONS)

    def synthesize_scope_target_file(self, scope: str, task: Task) -> str | None:
        """Create a conservative target filename inside an empty declared scope."""
        normalized_scope = str(scope or "").strip().replace("\\", "/").rstrip("/")
        if not normalized_scope or os.path.isabs(normalized_scope) or ".." in normalized_scope.split("/"):
            return None
        if path_predicates.is_concrete_target_file_path(normalized_scope):
            return normalized_scope
        slug = path_predicates.task_slug(task)
        if path_predicates.looks_like_test_scope(normalized_scope):
            filename = path_predicates.test_filename(slug, self.preferred_source_extension())
        else:
            filename = f"{slug}{self.preferred_source_extension()}"
        return f"{normalized_scope}/{filename}"

    def preferred_source_extension(self) -> str:
        """Infer the most suitable source extension for new scope targets."""
        if os.path.isfile(os.path.join(self.workspace, "tsconfig.json")):
            return ".ts"
        if os.path.isfile(os.path.join(self.workspace, "pyproject.toml")):
            return ".py"
        if os.path.isfile(os.path.join(self.workspace, "package.json")):
            return ".js"
        return ".ts"

    def normalize_scope_paths(self, task: Task) -> list[str]:
        """Return directory/module scopes declared by PM without treating them as files."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        scopes: list[str] = []
        seen: set[str] = set()
        for key in ("scope_paths", "write_scope"):
            value = metadata.get(key)
            if not isinstance(value, list):
                continue
            for raw in value:
                path = str(raw or "").strip().replace("\\", "/").rstrip("/")
                if not path or path in seen:
                    continue
                seen.add(path)
                scopes.append(path)
        return scopes
