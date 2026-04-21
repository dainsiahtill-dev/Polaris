"""Hypothesis Generator - 假设生成器。

基于错误上下文生成可能的原因假设。
"""

from __future__ import annotations

import hashlib
import uuid
from typing import ClassVar

from polaris.cells.roles.kernel.internal.debug_strategy.models import (
    ErrorContext,
    Hypothesis,
)
from polaris.cells.roles.kernel.internal.debug_strategy.types import ErrorCategory


class HypothesisGenerator:
    """假设生成器：基于错误上下文生成可能的原因假设。

    使用启发式规则和模式匹配生成假设。
    """

    # 假设模板库
    _HYPOTHESIS_TEMPLATES: ClassVar[dict[ErrorCategory, list[dict[str, str]]]] = {
        ErrorCategory.SYNTAX_ERROR: [
            {
                "description": "代码缩进不正确或混用了空格和Tab",
                "test_approach": "检查缩进一致性，使用统一缩进",
            },
            {
                "description": "缺少必要的括号或引号",
                "test_approach": "检查括号匹配，验证引号闭合",
            },
            {
                "description": "使用了保留字作为标识符",
                "test_approach": "检查变量名是否为Python保留字",
            },
        ],
        ErrorCategory.RUNTIME_ERROR: [
            {
                "description": "变量未定义就被使用",
                "test_approach": "检查变量定义位置和使用位置",
            },
            {
                "description": "函数返回了意外的None",
                "test_approach": "检查所有返回路径",
            },
            {
                "description": "列表/字典索引越界",
                "test_approach": "检查索引范围",
            },
        ],
        ErrorCategory.LOGIC_ERROR: [
            {
                "description": "条件判断逻辑错误",
                "test_approach": "检查条件表达式",
            },
            {
                "description": "循环边界条件错误",
                "test_approach": "检查循环起始和结束条件",
            },
            {
                "description": "算法实现有误",
                "test_approach": "对比算法伪代码和实现",
            },
        ],
        ErrorCategory.TIMING_ERROR: [
            {
                "description": "异步操作未等待完成",
                "test_approach": "检查await和async使用",
            },
            {
                "description": "资源未就绪就被使用",
                "test_approach": "添加就绪检查",
            },
            {
                "description": "竞态条件导致状态不一致",
                "test_approach": "添加同步机制",
            },
        ],
        ErrorCategory.RESOURCE_ERROR: [
            {
                "description": "文件路径不正确",
                "test_approach": "验证文件路径存在性",
            },
            {
                "description": "资源已被占用",
                "test_approach": "检查资源释放",
            },
            {
                "description": "内存不足",
                "test_approach": "检查内存使用",
            },
        ],
        ErrorCategory.PERMISSION_ERROR: [
            {
                "description": "当前用户权限不足",
                "test_approach": "检查用户权限",
            },
            {
                "description": "文件权限设置错误",
                "test_approach": "检查文件权限",
            },
            {
                "description": "SELinux/AppArmor限制",
                "test_approach": "检查安全策略",
            },
        ],
        ErrorCategory.NETWORK_ERROR: [
            {
                "description": "网络连接中断",
                "test_approach": "检查网络连通性",
            },
            {
                "description": "DNS解析失败",
                "test_approach": "检查DNS配置",
            },
            {
                "description": "防火墙阻止连接",
                "test_approach": "检查防火墙规则",
            },
        ],
        ErrorCategory.UNKNOWN_ERROR: [
            {
                "description": "依赖库版本不兼容",
                "test_approach": "检查依赖版本",
            },
            {
                "description": "环境配置问题",
                "test_approach": "检查环境变量",
            },
            {
                "description": "第三方服务故障",
                "test_approach": "检查服务状态",
            },
        ],
    }

    def generate_hypotheses(
        self,
        context: ErrorContext,
        category: ErrorCategory,
        max_hypotheses: int = 5,
    ) -> list[Hypothesis]:
        """生成可能的原因假设。

        Args:
            context: 错误上下文
            category: 错误分类
            max_hypotheses: 最大假设数量

        Returns:
            假设列表
        """
        hypotheses = []
        templates = self._HYPOTHESIS_TEMPLATES.get(category, [])

        # 基于模板生成假设
        for i, template in enumerate(templates[:max_hypotheses]):
            hypothesis_id = self._generate_hypothesis_id(context, i)
            hypothesis = Hypothesis(
                hypothesis_id=hypothesis_id,
                description=template["description"],
                confidence=self._calculate_confidence(context, template),
                test_approach=template["test_approach"],
                validation_criteria=[
                    f"验证{template['description']}",
                    "测试修复方案",
                    "验证无回归",
                ],
                related_patterns=[],
            )
            hypotheses.append(hypothesis)

        # 基于错误消息生成额外假设
        additional = self._generate_from_error_message(context)
        for hyp in additional:
            if len(hypotheses) < max_hypotheses:
                hypotheses.append(hyp)

        # 按置信度排序
        hypotheses.sort(key=lambda h: h.confidence, reverse=True)

        return hypotheses[:max_hypotheses]

    def _generate_hypothesis_id(self, context: ErrorContext, index: int) -> str:
        """生成假设唯一ID。"""
        content = f"{context.error_type}:{context.error_message}:{index}"
        hash_part = hashlib.md5(content.encode()).hexdigest()[:8]
        return f"hyp_{hash_part}"

    def _calculate_confidence(self, context: ErrorContext, template: dict[str, str]) -> float:
        """计算假设的置信度。"""
        base_confidence = 0.7

        # 根据错误消息匹配度调整
        error_msg = context.error_message.lower()
        template_desc = template["description"].lower()

        # 计算关键词匹配
        keywords = set(template_desc.split())
        matches = sum(1 for kw in keywords if kw in error_msg)
        match_ratio = matches / len(keywords) if keywords else 0

        confidence = base_confidence + (match_ratio * 0.2)
        return min(0.95, max(0.3, confidence))

    def _generate_from_error_message(self, context: ErrorContext) -> list[Hypothesis]:
        """基于错误消息生成假设。"""
        hypotheses = []
        error_msg_lower = context.error_message.lower()

        # 根据错误消息中的关键词生成假设
        if "none" in error_msg_lower or "null" in error_msg_lower:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description="变量为None/Null导致的问题",
                    confidence=0.8,
                    test_approach="添加空值检查",
                    validation_criteria=["验证空值处理", "测试正常路径"],
                    related_patterns=["null_pointer", "none_type"],
                )
            )

        if "index" in error_msg_lower or "range" in error_msg_lower:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description="索引越界或范围错误",
                    confidence=0.75,
                    test_approach="检查索引范围",
                    validation_criteria=["验证边界条件", "测试空集合"],
                    related_patterns=["index_error", "range_error"],
                )
            )

        if "key" in error_msg_lower:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description="字典键不存在",
                    confidence=0.75,
                    test_approach="检查键存在性",
                    validation_criteria=["验证键访问", "测试缺失键处理"],
                    related_patterns=["key_error", "missing_key"],
                )
            )

        if "type" in error_msg_lower:
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp_{uuid.uuid4().hex[:8]}",
                    description="类型不匹配",
                    confidence=0.7,
                    test_approach="检查类型注解",
                    validation_criteria=["验证类型一致性", "测试类型转换"],
                    related_patterns=["type_error", "type_mismatch"],
                )
            )

        return hypotheses


__all__ = ["HypothesisGenerator"]
