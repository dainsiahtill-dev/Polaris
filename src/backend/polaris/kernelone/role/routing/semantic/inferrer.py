"""两段式混合路由引擎 (Hybrid Semantic Router).

Phase 1: 极速规则匹配 (Fast Path) - 毫秒级响应,置信度 > 0.8 直接返回
Phase 2: 语义向量/LLM 分类器 (Slow Path) - 当 Fast Path 置信度不足时触发
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


class SemanticIntentInferer:
    """两段式意图推断引擎"""

    # 意图关键词映射
    INTENT_KEYWORDS = {
        "implement": ["实现", "写代码", "implement", "write code", "创建", "build"],
        "create": ["创建", "新建", "create", "new"],
        "fix": ["修复", "fix", "bug", "错误", "报错"],
        "improve": ["改进", "improve", "优化", "enhance"],
        "optimize": ["优化", "optimize", "性能", "performance"],
        "refactor": ["重构", "refactor", "重写"],
        "review": ["审查", "review", "检查", "看看"],
        "audit": ["审计", "audit", "安全"],
        "design": ["设计", "架构", "design", "architecture", "规划"],
        "deploy": ["部署", "deploy", "发布", "上线"],
        "analyze": ["分析", "analyze", "看看", "检查"],
    }

    # 领域关键词映射
    DOMAIN_KEYWORDS = {
        "python": ["python", "django", "flask", "fastapi", " tornado"],
        "typescript": ["typescript", "react", "vue", "前端", "angular"],
        "rust": ["rust", "cargo"],
        "devops": ["docker", "k8s", "kubernetes", "ci/cd", "jenkins", "gitlab"],
        "security": ["安全", "security", "漏洞", "加密", "auth"],
        "data": ["data", "database", "sql", "mongodb", "数据"],
        "ml": ["ml", "machine learning", "ai", "模型", "tensor", "pytorch"],
        "golang": ["golang", "go ", " go/"],
        "java": ["java", "spring"],
    }

    # 意图 → 任务类型映射
    INTENT_TO_TASK = {
        "implement": "new_code",
        "create": "new_crate",
        "fix": "bug_fix",
        "improve": "system_improvement",
        "optimize": "performance_critical",
        "refactor": "refactor",
        "review": "code_review",
        "audit": "security_review",
        "design": "architecture_design",
        "deploy": "deployment",
        "analyze": "analysis",
    }

    def infer(self, message: str) -> "IntentInferenceResult":
        """执行两段式意图推断"""
        rule_result = self._fast_path_match(message)
        if rule_result.confidence > 0.8:
            return rule_result
        return self._semantic_infer(message)

    def _fast_path_match(self, message: str) -> "IntentInferenceResult":
        """Phase 1: 关键词/正则快速匹配"""
        message_lower = message.lower()

        intent = self._match_keywords(message_lower, self.INTENT_KEYWORDS)
        domain = self._match_keywords(message_lower, self.DOMAIN_KEYWORDS)

        # 计算置信度
        if intent and domain:
            confidence = 1.0
        elif intent or domain:
            confidence = 0.6
        else:
            confidence = 0.3

        return IntentInferenceResult(
            intent=intent or "analyze",
            domain=domain or "general",
            task_type=self.INTENT_TO_TASK.get(intent or "", "default"),
            confidence=confidence,
            method="rule_based",
        )

    def _semantic_infer(self, message: str) -> "IntentInferenceResult":
        """Phase 2: 语义推断 (Slow Path)

        TODO: 集成 embedding similarity 或 LLM classifier
        当前实现返回降级结果
        """
        logger.info(f"Semantic inference triggered for: {message[:50]}...")

        # 启发式降级:当 Fast Path 不足时
        message_lower = message.lower()

        # 复杂查询检测
        if any(kw in message_lower for kw in ["有没有", "是否", "能不能", "会不会", "风险", "leak"]):
            return IntentInferenceResult(
                intent="analyze",
                domain="security",
                task_type="security_review",
                confidence=0.7,
                method="semantic_heuristic",
            )

        return IntentInferenceResult(
            intent="analyze",
            domain="general",
            task_type="default",
            confidence=0.5,
            method="semantic_fallback",
        )

    def _match_keywords(self, text: str, keywords: dict[str, list[str]]) -> str | None:
        """前缀匹配关键词"""
        for key, words in keywords.items():
            for word in words:
                if word in text:
                    return key
        return None


@dataclass
class IntentInferenceResult:
    """两段式意图推断结果"""

    intent: str
    domain: str
    task_type: str
    confidence: float  # 0.0 - 1.0
    method: str  # "rule_based" | "semantic_llm"
