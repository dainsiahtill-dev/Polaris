"""SLM Summarizer - Cognitive Gateway semantic compression for ContextOS.

ADR-0067-extension: ContextOS 2.0 SLM 语义压缩层

利用宿主注入的 SLM 网关（本地/局域网小模型 via Ollama）进行语义压缩，
替代 LLMLingua 成为 Tier 1 智能摘要层。

特点:
- 语义保留: 使用 SLM 理解内容后生成压缩摘要
- 多场景适配: 针对不同内容类型使用不同 prompt 模板
- 超时熔断: 2.5s 超时保护，防止 SLM 冷启动阻塞
- 线程隔离: 在独立线程中运行 async 网关，兼容 sync 接口

依赖方向:
- 本模块只消费 ``summarizers.contracts`` / ``summarizers.slm_config`` 声明的
  窄协议（``SummarizerGatewayProtocol`` / ``SLMBinding``）。
- SLM 网关与 SLM 配置由拥有它们的 Cell（roles.kernel）在宿主装配时注入，
  KernelOne 底座不引用任何业务 Cell，也不自己复制配置默认值。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
from typing import Any

from polaris.kernelone.context.context_os.summarizers.contracts import (
    SLMConfigProtocol,
    SummarizationError,
    SummarizerGatewayProtocol,
    SummaryStrategy,
)
from polaris.kernelone.context.context_os.summarizers.slm_config import (
    get_default_slm_binding,
)

__all__ = [
    "SLMConfigProtocol",
    "SLMSummarizer",
    "SummarizerGatewayProtocol",
]

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

    Uses the SLM gateway installed by the host (or an explicitly injected
    gateway) for semantic compression of context content.  Falls back to
    simple truncation on timeout or health check failure.

    This summarizer runs the async gateway in a background thread to remain
    compatible with the sync SummarizerInterface.

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
        config: SLMConfigProtocol | None = None,
        timeout_seconds: float = 2.5,
        max_content_length: int = 8000,
        gateway: SummarizerGatewayProtocol | None = None,
    ) -> None:
        """Initialize SLMSummarizer.

        Args:
            config: SLM configuration object owned by the SLM provider
                (for example a roles.kernel ``TransactionConfig``).  When
                given, it is forwarded unchanged to the binding so every
                non-default value (base URL, provider, model, keep-alive,
                timeout, enable flag) reaches the owner intact.  When
                omitted, the installed binding's own configuration is used.
            timeout_seconds: Timeout for SLM calls (including health check).
            max_content_length: Pre-truncate content longer than this before sending to SLM.
            gateway: Optional gateway instance. If None, the gateway is
                resolved lazily from the installed SLM binding.
        """
        self._config = config
        self.timeout_seconds = timeout_seconds
        self.max_content_length = max_content_length
        self._gateway: SummarizerGatewayProtocol | None = gateway
        self._executor: concurrent.futures.ThreadPoolExecutor | None = concurrent.futures.ThreadPoolExecutor(
            max_workers=1
        )

    @property
    def config(self) -> SLMConfigProtocol | None:
        """Effective SLM configuration.

        The explicitly supplied configuration wins; otherwise the
        configuration owned by the installed binding is used.  Returns
        ``None`` when neither is available (the summarizer is then
        unconfigured and refuses to fabricate a gateway).
        """
        if self._config is not None:
            return self._config
        binding = get_default_slm_binding()
        if binding is None:
            return None
        return binding.config()

    @config.setter
    def config(self, value: SLMConfigProtocol | None) -> None:
        """Replace the explicit SLM configuration."""
        self._config = value

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
        """Resolve the SLM gateway.

        Resolution order:
            1. An explicitly injected gateway (used as-is).
            2. The gateway produced by the installed SLM binding, which
               receives the explicit configuration when one was supplied.

        The gateway is created lazily on first use: constructing the
        summarizer never connects to the model.
        """
        if self._gateway is not None:
            return self._gateway

        binding = get_default_slm_binding()
        if binding is None:
            raise SummarizationError(
                "SLM summarization unavailable: no SLM binding installed",
                strategy=self.strategy,
            )

        gateway = binding.gateway(self._config)
        self._gateway = gateway
        return gateway

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

        Runs the async gateway in a background thread to avoid
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
            True if SLM is enabled in the effective configuration.  An
            unconfigured summarizer (no explicit config and no installed
            binding) is never available.
        """
        config = self.config
        if config is None:
            return False
        return config.slm_enabled

    def get_compression_stats(self) -> dict[str, Any]:
        """Get compression statistics.

        Returns:
            Compression stats dict.
        """
        config = self.config
        return {
            "strategy": self.strategy.name,
            "slm_enabled": config.slm_enabled if config is not None else False,
            "slm_model": config.slm_model_name if config is not None else None,
            "slm_provider": config.slm_provider if config is not None else None,
            "slm_keep_alive": config.slm_keep_alive if config is not None else None,
            "timeout_seconds": self.timeout_seconds,
            "max_content_length": self.max_content_length,
            "available": self.is_available(),
        }
