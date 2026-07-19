"""PM 计划产物 mixin：写出 runtime/tasks/plan.json 并对提示词泄漏文本做脱敏。

本 mixin 由 :class:`PMAdapter` 组合；方法体与原 ``pm_adapter.py`` 100% 一致（无损迁移）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.storage import resolve_runtime_path

from ._protocol import _PMAdapterMixinBase
from .pm_text_utils import (
    _PM_PLAN_DIRECTIVE_REDACTED,
    _PM_PLAN_FORBIDDEN_TEXT_REPLACEMENTS,
)

_PM_PLAN_ARTIFACT_SCHEMA_VERSION = "pm.plan_artifact.v1"


class PMPlanArtifactMixin(_PMAdapterMixinBase):
    """PM 计划产物 mixin：写出 runtime/tasks/plan.json 并对提示词泄漏文本做脱敏。"""

    def _write_plan_artifact(
        self,
        *,
        directive: str,
        task_contracts: list[dict[str, Any]],
        quality: dict[str, Any],
        quality_signals: list[dict[str, Any]] | None = None,
    ) -> Path:
        tasks_dir = Path(resolve_runtime_path(self.workspace, "runtime/tasks"))
        tasks_dir.mkdir(parents=True, exist_ok=True)
        plan_path = tasks_dir / "plan.json"
        sanitized_tasks = self._sanitize_plan_artifact_value(task_contracts)
        sanitized_signals = self._sanitize_plan_artifact_value(list(quality_signals or []))
        payload = {
            "schema_version": _PM_PLAN_ARTIFACT_SCHEMA_VERSION,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source": "pm_adapter_v2",
            "directive": _PM_PLAN_DIRECTIVE_REDACTED if directive else "",
            "quality_gate": {
                "score": int(quality.get("score") or 0),
                "critical_issue_count": (
                    len(cast("list", quality.get("critical_issues")))
                    if isinstance(quality, dict) and isinstance(quality.get("critical_issues"), list)
                    else 0
                ),
                "summary": str(quality.get("summary") or "").strip(),
                "signals": sanitized_signals,
            },
            "tasks": sanitized_tasks,
        }
        write_text_atomic(
            str(plan_path),
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return plan_path

    @classmethod
    def _sanitize_plan_artifact_value(cls, value: Any) -> Any:
        if isinstance(value, str):
            return cls._sanitize_plan_artifact_text(value)
        if isinstance(value, list):
            return [cls._sanitize_plan_artifact_value(item) for item in value]
        if isinstance(value, dict):
            return {key: cls._sanitize_plan_artifact_value(item) for key, item in value.items()}
        return value

    @staticmethod
    def _sanitize_plan_artifact_text(value: str) -> str:
        sanitized = str(value or "")
        for pattern, replacement in _PM_PLAN_FORBIDDEN_TEXT_REPLACEMENTS:
            sanitized = pattern.sub(replacement, sanitized)
        return sanitized.replace("提示词", "运行指令").replace("角色设定", "职责设定")
