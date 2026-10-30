"""Code-generation round planner collaborator for the Director worker.

Extracted verbatim from ``worker_executor.WorkerExecutor`` (G7 decomposition,
step 7). ``CodegenRoundPlanner`` turns a PM/ChiefEngineer task into the ordered
list of per-round file plans the worker drives one LLM call per round, plus the
construction-plan ``files`` hints consumed by the prompt builder.

The round precedence (verification-repair round wins; then
``construction_plan.rounds``; then ``construction_plan.file_plans``; then the
normalized target files), the non-concrete filtering, the ``[[]]`` collapse
semantics, and the chunk-size clamp (``min(value, 8)``, ``<=0`` -> 0) MUST stay
byte-identical to the original implementation; the bodies below are moved
verbatim.

This module depends only on the standard library + the pure ``path_predicates``
module + the ``VerificationRepair`` and ``TargetFileResolver`` collaborators +
domain (``Task``). It MUST NOT import ``code_generation_engine`` /
``file_apply_service`` at module top (lazy circular-import contract documented in
``worker_executor``).

All text operations MUST explicitly use UTF-8 encoding.
"""

from __future__ import annotations

import os
from typing import Any

from polaris.cells.director.tasking.internal import path_predicates
from polaris.cells.director.tasking.internal.target_file_resolver import TargetFileResolver
from polaris.cells.director.tasking.internal.verification_repair import VerificationRepair
from polaris.domain.entities import Task


class CodegenRoundPlanner:
    """Plan per-round file batches for a Director code-generation task."""

    def __init__(self, verification_repair: VerificationRepair, target_resolver: TargetFileResolver) -> None:
        self._verification_repair = verification_repair
        self._target_resolver = target_resolver

    def construction_file_plans(self, task: Task) -> list[dict[str, Any]]:
        """Get construction file plans from task metadata."""
        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        plan = metadata.get("construction_plan", {})
        if isinstance(plan, dict):
            files = plan.get("files", [])
            if isinstance(files, list):
                return files
        return []

    def build_code_generation_rounds(self, task: Task) -> list[list[dict[str, Any]]]:
        """Build code generation rounds from task metadata."""
        repair_paths = self._verification_repair.repair_target_paths(task)
        if repair_paths:
            return [[{"path": path, "repair": True} for path in repair_paths]]

        metadata = task.metadata if isinstance(task.metadata, dict) else {}
        plan = metadata.get("construction_plan", {})

        # Check for rounds in construction plan
        if isinstance(plan, dict):
            rounds = plan.get("rounds", [])
            if rounds:
                normalized_rounds: list[list[dict[str, Any]]] = []
                for round_items in rounds:
                    if not isinstance(round_items, list):
                        continue
                    normalized_items = [
                        item
                        for item in round_items
                        if isinstance(item, dict)
                        and path_predicates.is_concrete_target_file_path(str(item.get("path") or ""))
                    ]
                    if normalized_items:
                        normalized_rounds.append(normalized_items)
                return normalized_rounds or [[]]

        # Check for file_plans carried by older construction-plan payloads.
        if isinstance(plan, dict):
            file_plans = plan.get("file_plans", [])
            if file_plans:
                normalized_plans = [
                    item
                    for item in file_plans
                    if isinstance(item, dict)
                    and path_predicates.is_concrete_target_file_path(str(item.get("path") or ""))
                ]
                if not normalized_plans:
                    return [[]]
                chunk_size = self.effective_code_generation_round_chunk_size(normalized_plans)
                if chunk_size > 0:
                    return [normalized_plans[i : i + chunk_size] for i in range(0, len(normalized_plans), chunk_size)]
                return [normalized_plans]  # Single round with all files

        # Default: single round with all target files
        files = self._target_resolver.normalize_target_files(task)
        if files:
            file_plans = [{"path": f} for f in files]
            chunk_size = self.effective_code_generation_round_chunk_size(file_plans)
            if chunk_size > 0:
                return [file_plans[i : i + chunk_size] for i in range(0, len(file_plans), chunk_size)]
            return [file_plans]
        return [[]]

    @staticmethod
    def resolve_code_generation_round_chunk_size() -> int:
        """Resolve file count per code-generation round."""
        raw = (
            os.environ.get("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK")
            or os.environ.get("KERNELONE_CE_ROUND_FILE_CHUNK")
            or "2"
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            return 0
        return min(value, 8)

    @staticmethod
    def code_generation_round_chunk_configured() -> bool:
        """Return whether a caller explicitly configured target-file chunking.

        Dead code carried verbatim from ``WorkerExecutor`` (zero callers); see the
        G7 blueprint. Retained for behavior parity, not for live use.
        """
        return bool(
            os.environ.get("KERNELONE_DIRECTOR_TARGET_FILE_CHUNK") or os.environ.get("KERNELONE_CE_ROUND_FILE_CHUNK")
        )

    def effective_code_generation_round_chunk_size(self, file_plans: list[dict[str, Any]]) -> int:
        """Return the chunk size for this concrete file set."""
        _ = file_plans
        chunk_size = self.resolve_code_generation_round_chunk_size()
        if chunk_size <= 0:
            return 0
        return chunk_size

    @staticmethod
    def is_test_like_target_file(path: str) -> bool:
        """Return whether a target path is likely to produce expensive test/spec output.

        Delegates to ``path_predicates.is_test_like_target_file``. Carried verbatim
        from ``WorkerExecutor`` (the class-level copy had zero callers); see the G7
        blueprint.
        """
        return path_predicates.is_test_like_target_file(path)
