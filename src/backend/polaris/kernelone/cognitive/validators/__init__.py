"""Cognitive Validators — 设计质量验证与域隔离层。

提供 taste-skill 设计规则的工程化验证实现，包含：
- Domain Isolation: 文件扩展名/内容启发式路由，确保验证器只影响前端设计代码
- Anti-Slop Validation: 5 个独立 validator（font/color/content/layout/motion）
- Completeness Enforcement: 输出完整性检查，防止 AI 截断与骨架输出

使用方式:
    from polaris.kernelone.cognitive.validators import (
        CognitiveValidatorDispatcher,
        GenerationDomain,
        ValidationConfig,
        ValidationViolation,
        get_validator_dispatcher,
    )

    dispatcher = get_validator_dispatcher()
    violations = dispatcher.validate(
        file_path="src/components/Button.tsx",
        content=generated_code,
    )
"""

from polaris.kernelone.cognitive.validators.dispatcher import (
    CognitiveValidatorDispatcher,
    GenerationDomain,
    ValidationConfig,
    ValidationSeverity,
    ValidationViolation,
    get_validator_dispatcher,
    reset_validator_dispatcher,
)

__all__ = [
    "CognitiveValidatorDispatcher",
    "GenerationDomain",
    "ValidationConfig",
    "ValidationSeverity",
    "ValidationViolation",
    "get_validator_dispatcher",
    "reset_validator_dispatcher",
]
