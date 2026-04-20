"""Director 代码智能集成模块.

为 DirectorService 提供代码智能能力，包括上下文编译和变更验证.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from polaris.infrastructure.code_intelligence import CodeIntelligenceService


class DirectorCodeIntelMixin:
    """为 DirectorService 提供的代码智能 Mixin.

    使用示例:
        class DirectorService(DirectorCodeIntelMixin):
            def __init__(self, workspace: str, config: DirectorConfig):
                super().__init__(workspace)
                self.config = config

            async def execute_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
                # 1. 编译上下文
                context = self.compile_task_context(
                    task_description=task["description"],
                    target_files=task.get("target_files", []),
                )

                # 2. 执行任务...

                # 3. 验证变更
                verify_result = self.verify_task_changes(changed_files)
    """

    def __init__(self, workspace: str | Path, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._code_intel = CodeIntelligenceService(workspace)
        self._workspace = Path(workspace).resolve()

    def compile_task_context(
        self,
        task_description: str,
        target_files: list[str],
        iteration: int = 0,
        budget_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """为 Director 任务编译代码上下文.

        在 Director 执行代码修改前调用，获取相关代码上下文.

        Args:
            task_description: 任务描述
            target_files: 目标文件列表
            iteration: 当前迭代次数（用于控制索引刷新）
            budget_override: 预算覆盖参数

        Returns:
            包含以下键的字典:
            - top_files: 相关文件列表
            - snippets: 代码片段列表
            - semantic_graph: 语义关系图
            - rendered_prompt: 渲染后的提示文本
            - request_hash: 请求哈希
        """
        # 首次迭代时确保索引存在
        if iteration == 0:
            self._code_intel.ensure_index(force_full=False)

        default_budget = {
            "top_n_files": 12,
            "max_chars": 20000,
            "max_snippets": 8,
        }
        if budget_override:
            default_budget.update(budget_override)

        context = self._code_intel.get_context_for_task(
            task_description=task_description,
            changed_files=target_files,
            budget_override=default_budget,
        )

        return {
            "top_files": context.get("top_files", []),
            "snippets": context.get("snippets", []),
            "semantic_graph": context.get("semantic_graph", {}),
            "rendered_prompt": context.get("rendered_prompt", ""),
            "request_hash": context.get("request_hash", ""),
            "snapshot_path": context.get("snapshot_path", ""),
        }

    def verify_task_changes(self, changed_files: list[str]) -> dict[str, Any]:
        """验证任务变更.

        Args:
            changed_files: 变更的文件列表

        Returns:
            验证结果字典，包含 exit_code, passed, summary 等
        """
        if not changed_files:
            return {
                "exit_code": 0,
                "passed": True,
                "summary": "No files changed",
                "results": [],
            }

        return self._code_intel.verify_changes(changed_files, mode="evidence_run")

    def get_relevant_symbols(
        self,
        task_description: str,
        symbol_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """获取与任务相关的符号.

        Args:
            task_description: 任务描述
            symbol_type: 符号类型过滤（如 'function', 'class'）

        Returns:
            符号信息列表
        """
        # 获取扩展上下文以提取符号
        context = self._code_intel.get_context_for_task(
            task_description=task_description,
            budget_override={"top_n_files": 20, "max_snippets": 15},
        )

        symbols = []
        for snippet in context.get("snippets", []):
            sym_info = {
                "name": snippet.get("symbol", ""),
                "type": snippet.get("symbol_type", "unknown"),
                "file": snippet.get("file", ""),
                "line": snippet.get("line", 0),
                "signature": snippet.get("signature", ""),
            }
            if symbol_type and sym_info["type"] != symbol_type:
                continue
            symbols.append(sym_info)

        return symbols
