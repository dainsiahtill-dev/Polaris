"""Verification-repair collaborator for the Director worker.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 6). ``VerificationRepair`` turns the previous verification result carried on
a retry task (unresolved relative imports) into concrete repair target files and
a repair-prompt section, so a retry round can create the missing local modules.

The candidate-extension precedence (``.ts``/``.tsx``/``.js``/... + ``index`` files),
the dedup-by-parsed-tuple semantics, the ``candidate_files[:3]`` / ``records[:8]``
truncations, and the prompt-section wording MUST stay byte-identical to the
original implementation; the bodies below are moved verbatim.

``_resolve_workspace_file_path`` (via ``WorkspaceProbe``) only rejects traversal
(absolute / outside-workspace) paths -- it does NOT require the candidate file to
exist on disk; this collaborator preserves that exact behavior.

This module depends only on the standard library + the pure ``path_predicates``
module + the ``TargetFileResolver`` and ``WorkspaceProbe`` collaborators + domain
(``Task``). It MUST NOT import ``code_generation_engine`` / ``file_apply_service``
at module top (lazy circular-import contract documented in ``worker_executor``).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import os
from typing import Any

from polaris.cells.director.tasking.internal import path_predicates
from polaris.cells.director.tasking.internal.target_file_resolver import TargetFileResolver
from polaris.cells.director.tasking.internal.workspace_probe import WorkspaceProbe
from polaris.domain.entities import Task


def parse_unresolved_import_entry(entry: Any) -> tuple[str, str] | None:
    """Parse one ``"source: import_ref"`` verification entry.

    Returns ``(source_file, import_ref)`` only for relative imports (refs that
    start with ``.``); otherwise ``None``.
    """
    token = str(entry or "").strip()
    if ":" not in token:
        return None
    source_file, import_ref = token.split(":", maxsplit=1)
    source_file = source_file.strip().replace("\\", "/")
    import_ref = import_ref.strip().strip("`'\"")
    if not source_file or not import_ref.startswith("."):
        return None
    return source_file, import_ref


class VerificationRepair:
    """Derive repair targets from a retry task's previous verification result."""

    def __init__(self, target_resolver: TargetFileResolver, workspace_probe: WorkspaceProbe) -> None:
        self._target_resolver = target_resolver
        self._workspace_probe = workspace_probe

    def feedback(self, task: Task) -> dict[str, Any]:
        """Return previous verification diagnostics carried by the workflow retry."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        direct = metadata.get("previous_verification_result")
        if isinstance(direct, dict) and direct:
            return direct
        phase_context = metadata.get("phase_context")
        if isinstance(phase_context, dict):
            phase_verification = phase_context.get("verification_result")
            if isinstance(phase_verification, dict) and phase_verification:
                return phase_verification
        task_context = metadata.get("task_context")
        if isinstance(task_context, dict):
            previous = task_context.get("previous_verification_result")
            if isinstance(previous, dict) and previous:
                return previous
            nested_phase = task_context.get("phase_context")
            if isinstance(nested_phase, dict):
                nested_verification = nested_phase.get("verification_result")
                if isinstance(nested_verification, dict) and nested_verification:
                    return nested_verification
        return {}

    def candidate_paths_for_unresolved_import(self, source_file: str, import_ref: str) -> list[str]:
        source_dir = os.path.dirname(source_file.replace("\\", "/"))
        resolved = os.path.normpath(os.path.join(source_dir, import_ref)).replace("\\", "/")
        if not resolved or resolved.startswith("../") or os.path.isabs(resolved):
            return []
        leaf = os.path.basename(resolved)
        extension = os.path.splitext(leaf)[1].lower()
        if extension in {".ts", ".tsx", ".js", ".jsx", ".json", ".mjs", ".cjs"}:
            raw_candidates = [resolved]
        elif source_file.endswith((".ts", ".tsx")):
            raw_candidates = [
                f"{resolved}.ts",
                f"{resolved}.tsx",
                f"{resolved}.js",
                f"{resolved}.jsx",
                f"{resolved}.json",
                f"{resolved}/index.ts",
                f"{resolved}/index.tsx",
                f"{resolved}/index.js",
            ]
        elif source_file.endswith((".js", ".jsx", ".mjs", ".cjs")):
            raw_candidates = [
                f"{resolved}.js",
                f"{resolved}.jsx",
                f"{resolved}.ts",
                f"{resolved}.tsx",
                f"{resolved}.json",
                f"{resolved}/index.js",
                f"{resolved}/index.ts",
            ]
        else:
            raw_candidates = [resolved]

        candidates: list[str] = []
        seen: set[str] = set()
        for path in raw_candidates:
            normalized = path.replace("\\", "/")
            if normalized in seen or not path_predicates.is_concrete_target_file_path(normalized):
                continue
            if self._workspace_probe.resolve_workspace_file_path(normalized) is None:
                continue
            seen.add(normalized)
            candidates.append(normalized)
        return candidates

    def repair_path_allowed(self, task: Task, path: str) -> bool:
        normalized = str(path or "").strip().replace("\\", "/")
        if not normalized or self._workspace_probe.resolve_workspace_file_path(normalized) is None:
            return False
        target_files = set(self._target_resolver.normalize_target_files(task))
        if normalized in target_files:
            return True
        return any(
            path_predicates.path_under_scope(normalized, scope)
            for scope in self._target_resolver.normalize_scope_paths(task)
        )

    def unresolved_import_repair_records(self, task: Task) -> list[dict[str, Any]]:
        feedback = self.feedback(task)
        unresolved_raw = feedback.get("unresolved_imports")
        if not isinstance(unresolved_raw, list):
            return []
        records: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for raw_entry in unresolved_raw:
            parsed = parse_unresolved_import_entry(raw_entry)
            if parsed is None or parsed in seen:
                continue
            seen.add(parsed)
            source_file, import_ref = parsed
            if not self.repair_path_allowed(task, source_file):
                continue
            candidates = [
                candidate
                for candidate in self.candidate_paths_for_unresolved_import(source_file, import_ref)
                if self.repair_path_allowed(task, candidate)
            ]
            if not candidates:
                continue
            records.append(
                {
                    "source_file": source_file,
                    "import_ref": import_ref,
                    "candidate_files": candidates[:3],
                }
            )
        return records

    def repair_target_paths(self, task: Task) -> list[str]:
        records = self.unresolved_import_repair_records(task)
        paths: list[str] = []
        seen: set[str] = set()
        for record in records:
            record_paths = [
                str(record.get("source_file") or ""),
                *[str(path) for path in list(record.get("candidate_files") or [])[:1]],
            ]
            for path in record_paths:
                normalized = path.strip().replace("\\", "/")
                if normalized and normalized not in seen and self.repair_path_allowed(task, normalized):
                    seen.add(normalized)
                    paths.append(normalized)
        return paths

    def repair_prompt_section(self, task: Task) -> str:
        records = self.unresolved_import_repair_records(task)
        if not records:
            return "- No previous verification failure was provided."
        lines = [
            "- Previous verification failed with unresolved relative imports.",
            "- Resolve each issue by creating the listed candidate file or changing the source import to an existing local module.",
        ]
        for record in records[:8]:
            candidates = ", ".join(str(path) for path in list(record.get("candidate_files") or [])[:3])
            lines.append(
                f"- {record.get('source_file')} imports {record.get('import_ref')}; allowed repair candidate(s): {candidates}"
            )
        return "\n".join(lines)
