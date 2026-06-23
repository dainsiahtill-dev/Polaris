"""PM 任务板 mixin：在 TaskBoard 上创建/去重/匹配任务，并回填依赖关系。

本 mixin 由 :class:`PMAdapter` 组合；方法体与原 ``pm_adapter.py`` 100% 一致（无损迁移）。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from ._protocol import _PMAdapterMixinBase


class PMBoardTaskMixin(_PMAdapterMixinBase):
    """PM 任务板 mixin：在 TaskBoard 上创建/去重/匹配任务，并回填依赖关系。"""

    def _create_board_tasks(self, task_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created: list[dict[str, Any]] = []
        by_id: dict[str, int] = {}
        board_task_ids_by_contract_index: list[int] = []
        created_task_ids: set[int] = set()
        self._deduplicate_existing_board_tasks()
        existing_tasks = self.task_board.list_all()
        signature_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {}

        # Validate task contract dependencies
        validation_result = self._validate_task_contracts(task_contracts)
        validation_metadata: dict[str, Any] = {
            "plan_validation_is_valid": validation_result.is_valid,
            "plan_validation_errors": [
                {"rule_id": v.rule_id, "message": v.message, "location": v.location}
                for v in validation_result.violations
                if v.severity.name == "ERROR"
            ],
            "plan_validation_warnings": [
                {"rule_id": v.rule_id, "message": v.message, "location": v.location}
                for v in validation_result.violations
                if v.severity.name == "WARNING"
            ],
            "plan_validation_suggestions": list(validation_result.suggestions),
        }

        def _index_task(task_row: dict[str, Any]) -> None:
            title = str(task_row.get("subject") or "").strip()
            _raw_meta = task_row.get("metadata")
            metadata: dict[str, Any] = _raw_meta if isinstance(_raw_meta, dict) else {}
            goal = str(metadata.get("goal") or "").strip()
            signature = self._build_task_identity_signature(title=title, goal=goal)
            title_key = self._canonical_text(title)
            if signature:
                signature_index.setdefault(signature, []).append(task_row)
            if title_key:
                title_index.setdefault(title_key, []).append(task_row)

        for existing in existing_tasks:
            _index_task(existing.to_dict())

        for contract in task_contracts:
            _raw_contract_meta = contract.get("metadata")
            contract_metadata: dict[str, Any] = dict(_raw_contract_meta) if isinstance(_raw_contract_meta, dict) else {}
            contract_token = str(
                contract.get("id") or contract.get("task_id") or contract.get("pm_task_id") or ""
            ).strip()
            metadata = {
                "goal": contract.get("goal"),
                "scope": contract.get("scope"),
                "scope_paths": contract.get("scope_paths"),
                "target_files": contract.get("target_files"),
                "context_files": contract.get("context_files"),
                "steps": contract.get("steps"),
                "acceptance": contract.get("acceptance"),
                "phase": contract.get("phase"),
                "depends_on_external": contract.get("depends_on"),
                "assigned_to": contract.get("assigned_to"),
                "backlog_ref": contract.get("backlog_ref"),
                "quality_source": "pm_adapter_v2",
            }
            # Merge validation metadata (contract_metadata takes precedence)
            metadata = {**validation_metadata, **contract_metadata, **metadata}
            if contract_token:
                metadata["task_id"] = contract_token
                metadata["pm_task_id"] = contract_token
                metadata["source_task_id"] = contract_token
                metadata["external_task_id"] = contract_token
            subject = str(contract.get("title") or "").strip() or "Untitled task"
            description = str(contract.get("description") or "").strip()
            matched_id = self._find_existing_task_match(
                subject=subject,
                goal=str(contract.get("goal") or "").strip(),
                signature_index=signature_index,
                title_index=title_index,
            )
            if matched_id is not None and self._board_task_exists(matched_id):
                merged_metadata = dict(metadata)
                merged_metadata["pm_deduplicated"] = True
                merged_metadata["pm_last_contract_subject"] = subject
                existing_task = self.task_board.update(matched_id, metadata=merged_metadata)
                task = existing_task or self.task_board.get(matched_id)
                if task is None:
                    task = self.task_board.create(
                        subject=subject,
                        description=description,
                        metadata=metadata,
                    )
            else:
                task = self.task_board.create(
                    subject=subject,
                    description=description,
                    metadata=metadata,
                )
                _index_task(task.to_dict())

            board_task_ids_by_contract_index.append(int(task.id))
            token = str(contract.get("id") or "").strip()
            if token:
                by_id[token] = int(task.id)
            if int(task.id) not in created_task_ids:
                created_task_ids.add(int(task.id))
                created.append(task.to_dict())

        for idx, contract in enumerate(task_contracts):
            dependencies = contract.get("depends_on")
            dep_ids = dependencies if isinstance(dependencies, list) else []
            if not dep_ids:
                continue
            board_task_id = board_task_ids_by_contract_index[idx] if idx < len(board_task_ids_by_contract_index) else 0
            blocked_by: list[int] = []
            for dep in dep_ids:
                mapped = by_id.get(str(dep).strip())
                if mapped is not None and mapped != board_task_id:
                    blocked_by.append(mapped)
            if blocked_by and self._board_task_exists(board_task_id):
                self.task_board.update(
                    board_task_id,
                    blocked_by=blocked_by,
                    metadata={"resolved_depends_on_task_ids": blocked_by},
                )
                refreshed = self.task_board.get(board_task_id)
                if refreshed is not None:
                    refreshed_row = refreshed.to_dict()
                    for position, row in enumerate(created):
                        if int(row.get("id") or 0) == board_task_id:
                            created[position] = refreshed_row
                            break

        return created

    def _deduplicate_existing_board_tasks(self) -> None:
        tasks = [task.to_dict() for task in self.task_board.list_all()]
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in tasks:
            subject_key = self._canonical_text(str(row.get("subject") or ""))
            if not subject_key:
                continue
            grouped.setdefault(subject_key, []).append(row)

        for _, rows in grouped.items():
            if len(rows) <= 1:
                continue
            primary_id = self._pick_preferred_task_id(rows)
            if primary_id is None:
                continue
            for row in rows:
                task_id = int(row.get("id") or 0)
                if task_id <= 0 or task_id == primary_id:
                    continue
                status = str(row.get("status") or "").strip().lower()
                if status not in {"pending", "blocked", "in_progress", "failed"}:
                    continue
                self.task_board.update(
                    task_id,
                    status="cancelled",
                    metadata={
                        "dedup_merged_into": primary_id,
                        "dedup_reason": "pm_duplicate_subject",
                        "dedup_source": "pm_adapter",
                    },
                )

    @staticmethod
    def _canonical_text(value: str) -> str:
        token = str(value or "").strip().lower()
        if not token:
            return ""
        # 保留中英文和数字，移除符号噪声。
        normalized = "".join(ch for ch in token if ch.isalnum() or ("\u4e00" <= ch <= "\u9fff"))
        return normalized

    def _build_task_identity_signature(self, *, title: str, goal: str) -> str:
        left = self._canonical_text(title)
        right = self._canonical_text(goal)
        if left and right:
            return f"{left}::{right}"
        if left:
            return left
        if right:
            return right
        return ""

    @staticmethod
    def _pick_preferred_task_id(candidates: list[dict[str, Any]]) -> int | None:
        if not candidates:
            return None

        def _status_rank(row: dict[str, Any]) -> int:
            status = str(row.get("status") or "").strip().lower()
            if status == "in_progress":
                return 0
            if status in {"pending", "blocked"}:
                return 1
            if status == "completed":
                return 2
            if status in {"failed", "cancelled"}:
                return 3
            return 4

        ordered = sorted(
            candidates,
            key=lambda row: (_status_rank(row), -int(row.get("id") or 0)),
        )
        best = ordered[0] if ordered else None
        if not isinstance(best, dict):
            return None
        try:
            return int(best.get("id") or 0)
        except (RuntimeError, ValueError):
            return None

    def _find_existing_task_match(
        self,
        *,
        subject: str,
        goal: str,
        signature_index: dict[str, list[dict[str, Any]]],
        title_index: dict[str, list[dict[str, Any]]],
    ) -> int | None:
        signature = self._build_task_identity_signature(title=subject, goal=goal)
        if signature and signature in signature_index:
            matched = self._pick_preferred_task_id(signature_index[signature])
            if matched:
                return matched

        title_key = self._canonical_text(subject)
        if title_key and title_key in title_index:
            matched = self._pick_preferred_task_id(title_index[title_key])
            if matched:
                return matched

        if not title_key:
            return None

        # 高阈值模糊匹配：仅在标题极相近时复用，避免误并不同任务。
        best_id: int | None = None
        best_ratio = 0.0
        for indexed_title, rows in title_index.items():
            if not indexed_title:
                continue
            ratio = SequenceMatcher(None, title_key, indexed_title).ratio()
            if ratio < 0.93 or ratio < best_ratio:
                continue
            candidate_id = self._pick_preferred_task_id(rows)
            if candidate_id:
                best_ratio = ratio
                best_id = candidate_id

        return best_id

    def _parse_and_create_tasks(self, response: str) -> list[dict[str, Any]]:
        """兼容旧接口: 从文本中解析并创建任务."""
        contracts = self._extract_task_contracts(response, directive="")
        normalized, _quality = self._evaluate_contract_quality(contracts)
        return self._create_board_tasks(normalized)
