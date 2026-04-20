"""Chief Engineer 角色适配器

实现 Chief Engineer 角色的统一编排接口。
"""

from __future__ import annotations

from typing import Any

from polaris.bootstrap.config import get_settings
from polaris.cells.llm.dialogue.public.service import generate_role_response

from .base import BaseRoleAdapter


class ChiefEngineerAdapter(BaseRoleAdapter):
    """Chief Engineer 角色适配器

    职责：
    - 技术分析
    - 架构评估
    - 技术决策
    - 代码审查（技术深度）
    """

    @property
    def role_id(self) -> str:
        return "chief_engineer"

    def get_capabilities(self) -> list[str]:
        return [
            "technical_analysis",
            "architecture_review",
            "tech_decision",
            "deep_code_review",
        ]

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """执行 Chief Engineer 任务

        Args:
            task_id: 任务标识
            input_data: 包含 analysis_type, target
            context: 执行上下文
        """
        analysis_type = input_data.get("analysis_type", "technical_review")
        target = input_data.get("target", "")

        self._update_task_progress(task_id, "analyzing")

        try:
            # 构建分析消息
            message = self._build_analysis_message(analysis_type, target)

            # 调用 Chief Engineer 角色
            settings = get_settings()
            response = await generate_role_response(
                workspace=self.workspace,
                settings=settings,
                role=self.role_id,
                message=message,
                context=None,
                validate_output=False,
                max_retries=1,
            )
            content = str(response.get("response") or "") if isinstance(response, dict) else str(response or "")

            # 解析分析结果
            analysis = self._parse_analysis_result(content)

            self._update_task_progress(task_id, "completed")

            return {
                "success": True,
                "analysis_type": analysis_type,
                "target": target,
                "recommendation": analysis.get("recommendation", ""),
                "risks": analysis.get("risks", []),
                "alternatives": analysis.get("alternatives", []),
                "content_length": len(content),
            }

        except (RuntimeError, ValueError) as e:
            return {
                "success": False,
                "analysis_type": analysis_type,
                "error": str(e),
            }

    def _build_analysis_message(self, analysis_type: str, target: str) -> str:
        """构建 Chief Engineer 角色消息"""
        lines = [
            f"分析类型: {analysis_type}",
            f"分析目标: {target}",
            "",
            "请进行技术分析并输出以下格式:",
            "",
            "技术评估:",
            "[详细评估内容]",
            "",
            "建议方案: [推荐方案]",
            "",
            "风险评估:",
            "- [风险点]: [风险等级] - [缓解措施]",
            "",
            "备选方案:",
            "1. [方案描述]: [适用场景]",
        ]

        return "\n".join(lines)

    def _parse_analysis_result(self, content: str) -> dict[str, Any]:
        """解析分析结果"""
        lines = content.split("\n")

        recommendation = ""
        risks = []
        alternatives = []

        current_section = None

        for line in lines:
            line = line.strip()
            if line.startswith("建议方案:"):
                recommendation = line.replace("建议方案:", "").strip()
                current_section = None
            elif "风险" in line:
                current_section = "risks"
            elif "备选" in line or "替代" in line:
                current_section = "alternatives"
            elif line.startswith(("- ", "* ", "1. ", "2. ")):
                item = line[2:].strip() if line[1] == " " else line[3:].strip()
                if current_section == "risks":
                    risks.append(item)
                elif current_section == "alternatives":
                    alternatives.append(item)

        return {
            "recommendation": recommendation,
            "risks": risks,
            "alternatives": alternatives,
        }
