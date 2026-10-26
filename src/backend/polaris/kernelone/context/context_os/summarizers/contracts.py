"""Summarizer Interface Contracts - 摘要器接口契约

ADR-0067: ContextOS 2.0 摘要策略选型
定义统一的摘要器接口，支持多种实现和运行时降级。

本模块只声明"能力"，不引用任何具体实现：KernelOne 底座消费这里的
Protocol，由拥有该能力的 Cell / 宿主在装配时注入实现。
"""

from __future__ import annotations

from enum import Enum, auto
from typing import Protocol, runtime_checkable


class SummaryStrategy(Enum):
    """摘要策略枚举

    按优先级排序: 越靠前的策略越智能，但越可能失败/耗时
    """

    GENERATIVE = auto()  # transformers (BART/Qwen) - 生成式摘要
    SLM = auto()  # Small Language Model - 本地/边缘认知压缩
    ORCHESTRATED = auto()  # LangChain Map-Reduce - 超长文档
    STRUCTURED = auto()  # Tree-sitter AST - 代码感知
    EXTRACTIVE = auto()  # sumy (TextRank) - 抽取式摘要
    TRUNCATION = auto()  # 紧急截断 - 绝对安全网


class SummarizationError(Exception):
    """摘要生成错误

    当摘要器无法生成满足要求的摘要时抛出。
    """

    def __init__(self, message: str, strategy: SummaryStrategy | None = None) -> None:
        super().__init__(message)
        self.strategy = strategy


@runtime_checkable
class SummarizerInterface(Protocol):
    """摘要器统一接口

    所有摘要器实现必须遵循此接口，以便在 TieredSummarizer 中互换使用。

    Example:
        ```python
        summarizer = SumySummarizer()
        result = summarizer.summarize(
            content=long_text,
            max_tokens=500,
            content_type="log",
        )
        ```
    """

    strategy: SummaryStrategy

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """生成摘要

        Args:
            content: 原始内容
            max_tokens: 目标 token 数 (指导值，不是硬截断)
            content_type: 内容类型 (text, code, log, json, dialogue)

        Returns:
            摘要后的内容

        Raises:
            SummarizationError: 当无法生成满足要求的摘要时
        """
        ...

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """估算输出 token 数

        用于预算规划，帮助调用方决定是否启用摘要。

        Args:
            input_tokens: 输入内容的 token 数

        Returns:
            预估的输出 token 数
        """
        ...

    def is_available(self) -> bool:
        """检查摘要器是否可用

        用于检查依赖是否安装 (如 transformers, tree-sitter)。

        Returns:
            True 如果摘要器可以正常使用
        """
        ...


@runtime_checkable
class SummarizerGatewayProtocol(Protocol):
    """SLM 摘要网关能力契约

    KernelOne 底座只消费这个协议，不引用任何具体网关实现。
    拥有该能力的 Cell (roles.kernel) 在宿主装配时注入真实实现。
    """

    async def is_slm_healthy(self) -> bool:
        """检查 SLM 后端是否健康

        Returns:
            True 表示 SLM 后端可以提供服务
        """
        ...

    async def compress_text(self, prompt: str, *, max_tokens: int) -> str:
        """使用 SLM 压缩文本

        Args:
            prompt: 待压缩的 prompt
            max_tokens: 目标 token 数

        Returns:
            压缩后的文本
        """
        ...


@runtime_checkable
class SLMConfigProtocol(Protocol):
    """SLM 运行配置的只读形状

    KernelOne 底座只读取这几个字段来报告 SLM 状态，因此这里声明它们
    的**只读**形状，而不是声明一个不透明的 Any。

    本协议**不复制任何默认值**：模型名、provider、base URL、超时等
    默认值仍然只有拥有者 (roles.kernel) 一处。拥有者可以把真实的配置
    对象 (例如 ``TransactionConfig``) 原样传给网关，本协议只保证这些
    字段可读。

    注意：拥有者可以在自己的 Cell 内把该对象校验/收窄为具体类型并
    原样使用；底座不依赖这个具体类型。
    """

    slm_enabled: bool
    """SLM 是否启用。"""

    slm_provider: str
    """SLM provider 键 (例如 ``"ollama"``)。"""

    slm_model_name: str
    """SLM 模型标识。"""

    slm_keep_alive: str
    """Provider 侧的模型驻留提示。"""
