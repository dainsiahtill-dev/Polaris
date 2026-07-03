"""PM 适配器 mixin 的静态类型协议与运行时基类切换.

``PMAdapter`` 由多个职责 mixin 组合而成；这些 mixin 之间会互相调用对方
定义的方法（例如 parsing 调用 normalization 的 ``_normalize_task_contract``），
并访问 ``BaseRoleAdapter`` 提供的实例成员（如 ``workspace`` / ``task_board``）。

为了在 ``mypy --strict`` 下让每个 mixin 都能静态看到这些兄弟方法与基类成员，
同时保持运行时真实的 ``BaseRoleAdapter`` MRO，本模块提供:

- ``_PMAdapterProtocol``：声明所有跨 mixin 方法签名 + 所用的基类成员（仅类型期）。
- ``_PMAdapterMixinBase``：在 ``TYPE_CHECKING`` 下等于 Protocol，运行时等于
  ``BaseRoleAdapter``。每个 mixin 继承它即可获得正确的静态可见性与运行时行为。

所有签名与原 ``pm_adapter.py`` 中 ``PMAdapter`` 的对应方法保持一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from polaris.kernelone.planning import PlanValidationResult

from ..base import BaseRoleAdapter

if TYPE_CHECKING:

    class _PMAdapterProtocol(Protocol):
        """Static surface shared by every PM mixin (type-check only)."""

        # --- BaseRoleAdapter members the mixins rely on -------------------
        workspace: str

        @property
        def role_id(self) -> str: ...

        @property
        def task_board(self) -> Any: ...

        def _board_task_exists(self, task_id: Any) -> bool: ...

        def _update_board_task(
            self,
            task_id: Any,
            *,
            status: str | None = None,
            metadata: dict[str, Any] | None = None,
        ) -> bool: ...

        def _update_task_progress(
            self,
            task_id: str,
            phase: str,
            current_file: str | None = None,
            event_code: str | None = None,
            event_status: str | None = None,
            event_reason: str | None = None,
            event_detail: str | None = None,
            event_refs: dict[str, Any] | None = None,
        ) -> None: ...

        def _append_runtime_stage_signals(
            self,
            *,
            stage: str,
            task_id: str,
            signals: list[dict[str, Any]],
            context: dict[str, Any] | None = None,
            source: str | None = None,
        ) -> str | None: ...

        # --- prompting ----------------------------------------------------
        def _response_text(self, response: Any) -> str: ...

        def _metadata_from_input(self, input_data: dict[str, Any]) -> dict[str, Any]: ...

        def _compact_text_for_prompt(self, text: str, *, max_chars: int) -> str: ...

        def _deterministic_pm_contracts_enabled(
            self,
            *,
            input_data: dict[str, Any],
            context: dict[str, Any],
        ) -> bool: ...

        def _build_pm_message(
            self,
            tasks: list[dict[str, Any]],
            directive: str,
            *,
            projection_hint: dict[str, Any] | None = None,
            directive_analysis: dict[str, Any] | None = None,
        ) -> str: ...

        def _build_pm_retry_message(
            self,
            *,
            directive: str,
            quality: dict[str, Any],
            previous_output: str,
            projection_hint: dict[str, Any] | None = None,
        ) -> str: ...

        def _analyze_directive_complexity(
            self,
            directive: str,
            context: dict[str, Any],
        ) -> dict[str, Any]: ...

        def _apply_meta_planning_hints(
            self,
            message: str,
            directive_analysis: dict[str, Any],
        ) -> str: ...

        # --- parsing ------------------------------------------------------
        def _extract_task_contracts(
            self,
            response: str,
            *,
            directive: str,
            projection_hint: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...

        def _extract_tasks_from_payload(self, payload: Any) -> list[Any]: ...

        def _extract_json_payload(self, response: str) -> Any: ...

        def _extract_tasks_from_sections(self, response: str, *, directive: str) -> list[dict[str, Any]]: ...

        def _extract_tasks_from_bullets(self, response: str, *, directive: str) -> list[dict[str, Any]]: ...

        # --- normalization ------------------------------------------------
        def _normalize_task_contract(
            self,
            raw: dict[str, Any],
            index: int,
            directive: str,
        ) -> dict[str, Any]: ...

        def _normalize_list(self, value: Any) -> list[str]: ...

        def _normalize_scope_path_list(self, values: list[str]) -> list[str]: ...

        def _normalize_scope_path_token(self, value: str) -> str: ...

        def _infer_scope_from_title(self, title: str) -> list[str]: ...

        def _derive_domain_token(self, directive: str) -> str: ...

        def _extract_domain_keywords(self, directive: str, *, limit: int = 4) -> list[str]: ...

        def _normalize_projection_project_slug(self, value: Any, *, default_value: str = "projection_lab") -> str: ...

        def _extract_projection_contract_hint(
            self,
            *,
            input_data: dict[str, Any],
            context: dict[str, Any],
            directive: str,
        ) -> dict[str, Any]: ...

        def _apply_projection_contract_hint(
            self,
            contracts: list[dict[str, Any]],
            *,
            projection_hint: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...

        def _evaluate_contract_quality(
            self,
            contracts: list[dict[str, Any]],
            *,
            directive: str = "",
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]: ...

        def _validate_task_contracts(self, task_contracts: list[dict[str, Any]]) -> PlanValidationResult: ...

        # --- synthesis ----------------------------------------------------
        def _build_projection_hint_contracts(
            self,
            *,
            directive: str,
            projection_hint: dict[str, Any],
        ) -> list[dict[str, Any]]: ...

        def _synthesize_task_contracts_from_directive(
            self,
            *,
            directive: str,
            projection_hint: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]: ...

        def _synthesize_placeholder_repair_contracts(
            self,
            *,
            directive: str,
            source_metadata: dict[str, Any],
        ) -> list[dict[str, Any]]: ...

        def _synthesize_frontend_test_repair_contracts(
            self,
            *,
            directive: str,
            source_metadata: dict[str, Any],
        ) -> list[dict[str, Any]]: ...

        def _extract_directive_file_paths(self, directive: str, *, limit: int) -> list[str]: ...

        def _infer_directive_related_module_paths(
            self, directive: str, scope_paths: list[str], *, limit: int
        ) -> list[str]: ...

        def _synthesize_frontend_workbench_contracts(
            self,
            *,
            directive: str,
            domain: str,
            source_metadata: dict[str, Any],
        ) -> list[dict[str, Any]]: ...

        # --- board tasks --------------------------------------------------
        def _create_board_tasks(self, task_contracts: list[dict[str, Any]]) -> list[dict[str, Any]]: ...

        def _deduplicate_existing_board_tasks(self) -> None: ...

        def _canonical_text(self, value: str) -> str: ...

        def _build_task_identity_signature(self, *, title: str, goal: str) -> str: ...

        def _pick_preferred_task_id(self, candidates: list[dict[str, Any]]) -> int | None: ...

        def _find_existing_task_match(
            self,
            *,
            subject: str,
            goal: str,
            signature_index: dict[str, list[dict[str, Any]]],
            title_index: dict[str, list[dict[str, Any]]],
        ) -> int | None: ...

        def _parse_and_create_tasks(self, response: str) -> list[dict[str, Any]]: ...

        # --- plan artifact ------------------------------------------------
        def _write_plan_artifact(
            self,
            *,
            directive: str,
            task_contracts: list[dict[str, Any]],
            quality: dict[str, Any],
            quality_signals: list[dict[str, Any]] | None = None,
        ) -> Path: ...

        def _sanitize_plan_artifact_value(self, value: Any) -> Any: ...

        def _sanitize_plan_artifact_text(self, value: str) -> str: ...

    _PMAdapterMixinBase = _PMAdapterProtocol
else:
    _PMAdapterMixinBase = BaseRoleAdapter


__all__ = ["_PMAdapterMixinBase"]
