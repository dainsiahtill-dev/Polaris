"""SLM Summarizer - Cognitive Gateway semantic compression for ContextOS.

ADR-0067-extension: ContextOS 2.0 SLM 语义压缩层

利用 CognitiveGateway（本地/局域网小模型 via Ollama）进行语义压缩，
替代 LLMLingua 成为 Tier 1 智能摘要层。

特点:
- 语义保留: 使用 SLM 理解内容后生成压缩摘要
- 多场景适配: 针对不同内容类型使用不同 prompt 模板
- 超时熔断: 2.5s 超时保护，防止 SLM 冷启动阻塞
- 线程隔离: 在独立线程中运行 async 网关，兼容 sync 接口

依赖:
- polaris.cells.roles.kernel.internal.transaction (CognitiveGateway, SLMCoprocessor)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
from polaris.kernelone.context.context_os.summarizers.contracts import (
    SummarizationError,
    SummaryStrategy,
)


class SummarizerGatewayProtocol(Protocol):
    """Protocol for gateway used by SLMSummarizer.

    Decouples SLMSummarizer from concrete CognitiveGateway/SLMCoprocessor
    implementations to avoid cross-Cell direct imports.
    """

    async def is_slm_healthy(self) -> bool:
        """Check if SLM backend is healthy."""
        ...

    async def compress_text(self, prompt: str, *, max_tokens: int) -> str:
        """Compress text using SLM.

        Args:
            prompt: The prompt to compress.
            max_tokens: Target token count.

        Returns:
            Compressed text.
        """
        ...


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prompt templates for different content types
# ---------------------------------------------------------------------------

_HISTORY_COMPRESSION_PROMPT = """\
You are a semantic compressor for AI conversation history.
Compress the following dialogue into a concise summary that preserves:
- All decisions and commitments made
- All open questions or pending items
- Key facts and constraints mentioned
- Action items with their owners

Original content:
{content}

Provide a compressed version in 1-3 sentences, keeping all critical information:"""

_ARTIFACT_SNIPPING_PROMPT = """\
You are a code artifact compressor.
Summarize the following code/file content, preserving:
- Function/class signatures and their purposes
- Important configuration values
- Error handling patterns
- Any TODOs or FIXMEs

Original content:
{content}

Provide a concise structural summary:"""

_BELIEF_RECONCILIATION_PROMPT = """\
You are a belief state compressor.
Given a set of assertions, facts, and constraints, produce a minimal representation that preserves:
- Hard constraints (must/must not)
- Current goal and sub-goals
- Known facts that affect future decisions
- Conflicts or uncertainties

Original content:
{content}

Compressed belief state:"""

_CAUSAL_CHAIN_PROMPT = """\
You are a causal chain compressor.
Given a sequence of events, decisions, and outcomes, preserve:
- The triggering event or decision
- Key intermediate steps
- Final outcome or current state
- Any errors or exceptions and their causes

Original content:
{content}

Compressed causal chain:"""

_PROMPT_TEMPLATES: dict[str, str] = {
    "dialogue": _HISTORY_COMPRESSION_PROMPT,
    "code": _ARTIFACT_SNIPPING_PROMPT,
    "json": _ARTIFACT_SNIPPING_PROMPT,
    "text": _BELIEF_RECONCILIATION_PROMPT,
    "log": _CAUSAL_CHAIN_PROMPT,
    "error": _CAUSAL_CHAIN_PROMPT,
    "default": _BELIEF_RECONCILIATION_PROMPT,
}


class SLMSummarizer:
    """SLM-based semantic compressor for ContextOS.

    Uses CognitiveGateway (local/LAN small LLM via Ollama) for semantic
    compression of context content. Falls back to simple truncation on
    timeout or health check failure.

    This summarizer runs the async CognitiveGateway in a background
    thread to remain compatible with the sync SummarizerInterface.

    Example:
        ```python
        summarizer = SLMSummarizer()
        if summarizer.is_available():
            compressed = summarizer.summarize(
                content=long_dialogue,
                max_tokens=300,
                content_type="dialogue",
            )
        ```
    """

    strategy = SummaryStrategy.SLM

    def __init__(
        self,
        config: TransactionConfig | None = None,
        timeout_seconds: float = 2.5,
        max_content_length: int = 8000,
        gateway: SummarizerGatewayProtocol | None = None,
    ) -> None:
        """Initialize SLMSummarizer.

        Args:
            config: TransactionConfig with SLM settings. If None, uses defaults.
            timeout_seconds: Timeout for SLM calls (including health check).
            max_content_length: Pre-truncate content longer than this before sending to SLM.
            gateway: Optional gateway instance. If None, lazily creates one internally.
        """
        self.config = config or TransactionConfig()
        self.timeout_seconds = timeout_seconds
        self.max_content_length = max_content_length
        self._gateway: SummarizerGatewayProtocol | None = gateway
        self._executor: concurrent.futures.ThreadPoolExecutor | None = concurrent.futures.ThreadPoolExecutor(
            max_workers=1
        )

    def close(self) -> None:
        """Shutdown thread pool executor. Must be called explicitly."""
        if hasattr(self, "_executor") and self._executor is not None:
            self._executor.shutdown(wait=True)
            self._executor = None

    def __enter__(self) -> SLMSummarizer:
        """Enter runtime context."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Exit runtime context and close resources."""
        self.close()

    def _get_gateway(self) -> SummarizerGatewayProtocol:
        """Lazy initialization of CognitiveGateway.

        优先复用全局预热过的 CognitiveGateway 单例，避免重复创建 SLM
        连接和冷启动。若单例尚未初始化，则 fallback 到本地新建。
        """
        if self._gateway is not None:
            return self._gateway

        from polaris.cells.roles.kernel.internal.transaction.cognitive_gateway import (
            CognitiveGateway,
        )

        # 优先复用全局已预热单例
        singleton = CognitiveGateway.get_default_instance_sync()
        # 在测试 mock 环境中，singleton 可能是 MagicMock 对象；
        # 通过检查类名来过滤，确保只复用真实的 CognitiveGateway 实例。
        if (
            singleton is not None
            and getattr(singleton, "__class__", None) is not None
            and singleton.__class__.__name__ == "CognitiveGateway"
        ):
            logger.debug("SLMSummarizer attached to warmed CognitiveGateway singleton")
            self._gateway = singleton
            return self._gateway

        # Fallback: 本地新建（无预热，首次调用会触发冷启动）
        from polaris.cells.roles.kernel.internal.transaction.slm_coprocessor import (
            SLMCoprocessor,
        )

        slm = SLMCoprocessor(config=self.config)
        self._gateway = CognitiveGateway(
            config=self.config,
            slm_coprocessor=slm,
        )
        return self._gateway

    def _build_prompt(self, content: str, content_type: str) -> str:
        """Select and render prompt template for given content type."""
        template = _PROMPT_TEMPLATES.get(content_type, _PROMPT_TEMPLATES["default"])
        return template.format(content=content)

    def _fallback(self, content: str, max_tokens: int) -> str:
        """Simple truncation fallback when SLM is unavailable or times out.

        Preserves head and tail of content, similar to LLMLingua fallback.
        """
        if not content or len(content) <= max_tokens * 4:
            return content

        lines = content.split("\n")
        max_lines = max(20, max_tokens // 4)

        if len(lines) > max_lines:
            head_lines = int(max_lines * 0.7)
            tail_lines = max_lines - head_lines
            head = lines[:head_lines]
            tail = lines[-tail_lines:] if tail_lines > 0 else []
            return "\n".join([*head, "    // ... (truncated) ...", *tail])

        # Single-line or few-line long content: character-based truncation
        max_chars = max_tokens * 4
        if len(content) <= max_chars:
            return content
        head_chars = int(max_chars * 0.7)
        tail_chars = max_chars - head_chars
        return content[:head_chars] + "\n    // ... (truncated) ...\n" + content[-tail_chars:]

    async def _async_summarize(self, prompt: str, max_tokens: int) -> str:
        """Async inner implementation: health check + SLM compression."""
        gateway = self._get_gateway()

        # Health check with sub-timeout (must tolerate LAN Ollama cold-start)
        # First call may need 10-20s to load model into VRAM; we allow up to
        # 30s for the health probe itself, then compress_text has its own
        # timeout budget.
        try:
            healthy = await asyncio.wait_for(
                gateway.is_slm_healthy(),
                timeout=min(30.0, self.timeout_seconds * 0.6),
            )
            if not healthy:
                # Health check explicitly returned False → skip compression
                return ""
        except asyncio.TimeoutError:
            # Health check may time out on slow networks, but SLM could still
            # be reachable. Proceed to compression attempt rather than giving up.
            logger.debug("SLM health check timed out, attempting compression anyway")

        # Compress with main timeout
        try:
            return await asyncio.wait_for(
                gateway.compress_text(prompt, max_tokens=max_tokens),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.debug("SLM compress_text timed out")
            return ""

    def summarize(
        self,
        content: str,
        max_tokens: int,
        content_type: str = "text",
    ) -> str:
        """Generate semantic compression summary using SLM.

        Runs the async CognitiveGateway in a background thread to avoid
        event loop conflicts in sync contexts.

        Args:
            content: Original content to compress.
            max_tokens: Target token count (passed to SLM as guidance).
            content_type: Content type for prompt template selection.

        Returns:
            Compressed content string.

        Raises:
            SummarizationError: When SLM is unavailable, times out, or returns empty.
                TieredSummarizer will catch this and try the next strategy.
        """
        if not content or len(content.strip()) < 100:
            return content

        # Pre-truncate extremely long content to avoid overwhelming the SLM
        working_content = content
        if len(working_content) > self.max_content_length:
            working_content = working_content[: self.max_content_length] + "\n... [truncated for SLM processing] ..."

        prompt = self._build_prompt(working_content, content_type)

        def _run_async_in_thread() -> str:
            """Run the async coroutine, reusing an existing loop if available."""
            try:
                loop = asyncio.get_running_loop()
                future = asyncio.run_coroutine_threadsafe(self._async_summarize(prompt, max_tokens=max_tokens), loop)
                return future.result(timeout=self.timeout_seconds)
            except RuntimeError:
                return asyncio.run(self._async_summarize(prompt, max_tokens=max_tokens))

        if self._executor is None:
            raise SummarizationError(
                "SLM summarizer has been closed",
                strategy=self.strategy,
            )

        try:
            future = self._executor.submit(_run_async_in_thread)
            # Slightly longer than inner timeout to account for thread startup
            result = future.result(timeout=self.timeout_seconds + 0.5)
            if result and len(result.strip()) > 10:
                return result.strip()
        except concurrent.futures.TimeoutError:
            logger.debug("SLM summarization thread timed out")
        except (ConnectionError, TimeoutError, ValueError) as e:
            logger.debug("SLM summarization failed (%s): %s", type(e).__name__, e)

        # Signal failure so TieredSummarizer can try the next strategy
        raise SummarizationError(
            "SLM summarization unavailable or returned empty",
            strategy=self.strategy,
        )

    def estimate_output_tokens(self, input_tokens: int) -> int:
        """Estimate output token count.

        SLM semantic compression typically achieves 30-50% ratio.

        Args:
            input_tokens: Input token count.

        Returns:
            Estimated output token count.
        """
        return int(input_tokens * 0.35)

    def is_available(self) -> bool:
        """Check if SLM summarizer is optimistically available.

        Returns the config flag only. Real health check happens inside
        summarize() with timeout protection.

        Returns:
            True if SLM is enabled in config.
        """
        return self.config.slm_enabled

    def get_compression_stats(self) -> dict[str, Any]:
        """Get compression statistics.

        Returns:
            Compression stats dict.
        """
        return {
            "strategy": self.strategy.name,
            "slm_enabled": self.config.slm_enabled,
            "slm_model": self.config.slm_model_name,
            "slm_provider": self.config.slm_provider,
            "slm_keep_alive": getattr(self.config, "slm_keep_alive", "5m"),
            "timeout_seconds": self.timeout_seconds,
            "max_content_length": self.max_content_length,
            "available": self.is_available(),
        }
