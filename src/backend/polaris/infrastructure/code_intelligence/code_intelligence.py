"""Polaris 代码智能服务 - 封装 agent-accel 核心功能."""

from __future__ import annotations

from pathlib import Path
from typing import Any

# 导入 agent-accel 组件
from polaris.infrastructure.accel.config import resolve_effective_config
from polaris.infrastructure.accel.indexers import build_or_update_indexes
from polaris.infrastructure.accel.query.context_compiler import compile_context_pack
from polaris.infrastructure.accel.verify.orchestrator import run_verify


class CodeIntelligenceService:
    """代码智能服务 - ChiefEngineer 和 Director 的代码分析基础设施.

    职责:
    1. 代码索引管理 (构建/增量更新)
    2. 任务上下文生成 (compile_context_pack)
    3. 变更验证 (run_verify)
    4. 符号查询和依赖分析
    """

    def __init__(self, workspace: str | Path, accel_home: str | Path | None = None) -> None:
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.workspace = Path(workspace).resolve()
        metadata_dir = get_workspace_metadata_dir_name()
        self.accel_home = Path(accel_home) if accel_home else self.workspace / metadata_dir
        self._config: dict[str, Any] | None = None
        self._index_initialized = False

    @property
    def config(self) -> dict[str, Any]:
        """懒加载配置."""
        if self._config is None:
            self._config = resolve_effective_config(self.workspace)
            # 覆盖 accel_home 到 Polaris 目录
            if "runtime" not in self._config:
                self._config["runtime"] = {}
            self._config["runtime"]["accel_home"] = str(self.accel_home)
        return self._config

    def ensure_index(self, force_full: bool = False) -> dict[str, Any]:
        """确保索引已构建，返回 manifest.

        Args:
            force_full: 是否强制完整重建
        """
        mode = "build" if force_full else "update"
        full = force_full or not self._index_initialized

        manifest = build_or_update_indexes(
            project_dir=self.workspace,
            config=self.config,
            mode=mode,
            full=full,
        )
        self._index_initialized = True
        return manifest

    def get_context_for_task(
        self,
        task_description: str,
        changed_files: list[str] | None = None,
        hints: list[str] | None = None,
        budget_override: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """为任务生成代码上下文.

        这是 ChiefEngineer 和 Director 的主要集成点.
        """
        # 确保索引存在
        self.ensure_index()

        pack = compile_context_pack(
            project_dir=self.workspace,
            config=self.config,
            task=task_description,
            changed_files=changed_files or [],
            hints=hints or [],
            budget_override=budget_override,
        )
        return pack

    def verify_changes(
        self,
        changed_files: list[str],
        mode: str = "evidence_run",
    ) -> dict[str, Any]:
        """运行增量验证."""
        # Note: evidence_run mode is no longer a parameter in run_verify
        # The verification behavior is controlled via config
        return run_verify(
            project_dir=self.workspace,
            config=self.config,
            changed_files=changed_files,
        )

    def get_relevant_files(
        self,
        task_description: str,
        top_n: int = 10,
    ) -> list[dict[str, Any]]:
        """获取与任务最相关的文件列表 (用于 ChiefEngineer 分析范围确定)."""
        context = self.get_context_for_task(
            task_description=task_description,
            budget_override={"top_n_files": top_n},
        )
        return context.get("top_files", [])
