"""SLM Cognitive Coprocessor — 轻量级认知协处理器.

将局域网/本地小型 LLM 提升为系统级 NPU，负责边缘计算任务：
- 意图分类 refine
- 长日志降维
- JSON 语法修复
- 搜索查询扩展

Architecture:
- 通过 ProviderManager 获取已注册的 SLM provider（默认 ollama）
- 所有同步 I/O（requests.post）通过 asyncio.to_thread  offload
- 失败时优雅降级：返回默认值或原始输入
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from typing import Any

from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig

logger = logging.getLogger(__name__)

# Delayed import to avoid circular dependency at module load time


def _get_default_provider_manager() -> Any:
    from polaris.infrastructure.llm.providers.provider_registry import provider_manager

    return provider_manager


class SLMCoprocessor:
    """轻量级认知协处理器 (NPU)。

    使用低延迟、低成本的本地小模型，处理数据清洗、分类、降维等边缘计算任务。
    主模型（Claude/GPT-4）只负责架构决策和核心代码编写；
    所有洗菜、切菜、倒垃圾的工作交给 SLM 协处理器并行处理。
    """

    _default_instance: SLMCoprocessor | None = None
    _instance_lock: asyncio.Lock | None = None
    _thread_lock = threading.Lock()

    @classmethod
    def _get_instance_lock(cls) -> asyncio.Lock:
        """线程安全地获取/创建 asyncio.Lock（双重检查锁定）。"""
        if cls._instance_lock is None:
            with cls._thread_lock:
                if cls._instance_lock is None:
                    cls._instance_lock = asyncio.Lock()
        return cls._instance_lock

    def __init__(
        self,
        *,
        config: TransactionConfig | None = None,
        provider_manager: Any | None = None,
    ) -> None:
        self.config = config or TransactionConfig()
        self._provider_manager = provider_manager or _get_default_provider_manager()

    @classmethod
    async def default(cls) -> SLMCoprocessor:
        """Get the global singleton instance."""
        if cls._default_instance is None:
            async with cls._get_instance_lock():
                if cls._default_instance is None:
                    cls._default_instance = cls()
        return cls._default_instance

    @classmethod
    def reset_default(cls) -> None:
        """Reset singleton — primarily for test isolation."""
        cls._default_instance = None

    def _get_slm_client(self) -> Any | None:
        """获取 SLM provider 实例；若未启用或不可用则返回 None。"""
        if not self.config.slm_enabled:
            return None
        try:
            return self._provider_manager.get_provider_instance(self.config.slm_provider)
        except Exception:  # noqa: BLE001
            logger.debug(
                "SLM provider %s not available",
                self.config.slm_provider,
                exc_info=True,
            )
            return None

    def _build_slm_config(self) -> dict[str, Any]:
        """构建调用 SLM 时的 provider config。"""
        base_url = self.config.slm_base_url or os.environ.get("OLLAMA_HOST", "http://192.168.1.2:11434")
        cfg: dict[str, Any] = {
            "base_url": base_url,
            "timeout": self.config.slm_timeout,
            "api_key": "ollama",
        }
        if getattr(self.config, "slm_keep_alive", None):
            cfg["keep_alive"] = self.config.slm_keep_alive
        return cfg

    async def _invoke_slm(self, prompt: str, max_tokens: int | None = None) -> str:
        """调用 SLM，返回纯文本输出。失败时返回空字符串。"""
        client = self._get_slm_client()
        if client is None:
            return ""
        config = self._build_slm_config()
        if max_tokens is not None:
            config["max_tokens"] = max_tokens

        try:
            # invoke 是同步方法，offload 到线程避免阻塞事件循环
            result = await asyncio.to_thread(
                client.invoke,
                prompt=prompt,
                model=self.config.slm_model_name,
                config=config,
            )
            if result.ok:
                return str(result.output or "").strip()
            logger.debug("SLM invoke failed: %s", result.error)
        except Exception:  # noqa: BLE001
            logger.debug("SLM invoke exception", exc_info=True)
        return ""

    async def classify_intent(self, text: str, categories: list[str] | None = None) -> str:
        """使用 SLM 对文本进行意图分类。

        当 Regex + Embedding 仍无法确定意图时，调用本地小模型做最终 refine。
        """
        if not self.config.slm_enabled:
            return "UNKNOWN"

        cat_list = categories or [
            "STRONG_MUTATION",
            "DEBUG_AND_FIX",
            "DEVOPS",
            "WEAK_MUTATION",
            "TESTING",
            "PLANNING",
            "ANALYSIS_ONLY",
        ]
        categories_text = "\n".join(f"- {c}" for c in cat_list)
        prompt = (
            f"请将以下用户请求分类到最合适的意图类别中。\n"
            f"可选类别：\n{categories_text}\n\n"
            f"用户请求：{text}\n\n"
            f"只输出类别名称，不要解释。"
        )
        output = await self._invoke_slm(prompt, max_tokens=50)
        # 清理输出，只保留匹配的类别
        for cat in cat_list:
            if cat in output:
                return cat
        return "UNKNOWN"

    async def distill_long_logs(self, raw_logs: str, max_tokens: int = 500) -> str:
        """长日志降维 — 将冗长报错日志提炼为关键摘要。

        适用于 pytest 失败、编译错误等场景。
        """
        if not self.config.slm_enabled:
            # 降级：暴力截断尾部
            return raw_logs[-2000:] if len(raw_logs) > 2000 else raw_logs

        prompt = (
            f"提取以下报错日志的核心原因和关键堆栈，用中文简洁总结，限 {max_tokens} 字以内，不要废话：\n\n{raw_logs}"
        )
        return await self._invoke_slm(prompt, max_tokens=max_tokens)

    async def heal_json(self, broken_json: str) -> dict[str, Any] | None:
        """JSON 语法修复 — 修复 LLM 输出的 malformed JSON。

        适用于 tool call JSON 漏逗号、多括号等常见幻觉。
        """
        if not self.config.slm_enabled:
            return None

        prompt = f"修复以下文本使其成为合法的 JSON。只输出 JSON 本体，不要 markdown 代码块，不要解释：\n{broken_json}"
        output = await self._invoke_slm(prompt, max_tokens=2048)
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            logger.debug("SLM healed JSON is still invalid")
            return None

    async def expand_search_query(self, user_query: str) -> list[str]:
        """搜索查询扩展 — 将模糊自然语言翻译为精确关键词列表。

        适用于 repo_rg / search_code 等工具的 query 增强。
        """
        if not self.config.slm_enabled:
            return [user_query]

        prompt = (
            f"将以下用户查询扩展为 3-5 个相关的技术关键词/正则模式，"
            f"用于代码搜索。只输出关键词列表，每行一个：\n\n{user_query}"
        )
        output = await self._invoke_slm(prompt, max_tokens=200)
        if not output:
            return [user_query]

        keywords: list[str] = []
        for line in output.splitlines():
            line = line.strip().lstrip("- ").strip()
            if line:
                keywords.append(line)
        return keywords if keywords else [user_query]
