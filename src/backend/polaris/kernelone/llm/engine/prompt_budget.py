"""Polaris AI Platform - Token Budget Manager

按模型上下文上限进行预算决策，必要时触发上下文压缩。

架构：
- TokenBudgetManager: 纯预算决策器，负责计算预算和路由压缩请求
- TokenEstimator: 统一 token 估算
- CompressionRouter: 根据内容类型路由到最佳压缩器

Architecture note:
    This module uses port interfaces (RoleContextCompressorPort) from llm/ports.py
    to avoid circular dependencies with the context module.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from .contracts import CompressionResult, ModelSpec, TokenBudgetDecision
from .token_estimator import TokenEstimator

if TYPE_CHECKING:
    # Port interfaces (used via TYPE_CHECKING to avoid circular import)
    from polaris.kernelone.llm.ports import RoleContextCompressorPort

logger = logging.getLogger(__name__)


class CompressionRouter:
    """压缩路由器：根据内容类型和内容特征选择最佳压缩策略。

    Uses RoleContextCompressorPort to avoid circular dependencies with context module.
    """

    def __init__(
        self,
        workspace: str | None = None,
        role: str | None = None,
        *,
        # Port-based compressor injection (avoids circular import with context)
        compressor_port: RoleContextCompressorPort | None = None,
    ) -> None:
        self.workspace = workspace
        self.role = role
        self._compressor_port = compressor_port

    def route_and_compress(
        self,
        text: str,
        target_tokens: int,
        *,
        content_type: str = "general",
        compression_history: list[str] | None = None,
    ) -> tuple[str, CompressionResult]:
        """路由并执行压缩。

        Returns:
            (compressed_text, result)
        """
        history = compression_history or []
        original_tokens = TokenEstimator.estimate(text, content_type=content_type)

        # 1. 尝试 RoleContextCompressor（如果可用且适合）
        if "role_context" not in history and self._is_conversation_content(text):
            result = self._try_role_context_compressor(text, target_tokens)
            if result:
                return result.compressed_input, result

        # 2. 代码内容：使用 rule_compressor 风格
        if "code" not in history and self._is_code_content(text):
            compressed = self._compress_code(text, target_tokens)
            if compressed != text:
                result = CompressionResult(
                    compressed_input=compressed,
                    original_tokens=original_tokens,
                    compressed_tokens=TokenEstimator.estimate(compressed, content_type="code"),
                    strategy="code_rules",
                    notes=["applied code-specific compression (imports/comments)"],
                )
                return compressed, result

        # 3. 通用文本：行压缩
        if "line" not in history:
            compressed = self._compress_by_lines(text, target_tokens)
            if compressed != text:
                result = CompressionResult(
                    compressed_input=compressed,
                    original_tokens=original_tokens,
                    compressed_tokens=TokenEstimator.estimate(compressed),
                    strategy="line_compaction",
                    notes=["kept high-signal head/tail sections"],
                )
                return compressed, result

        # 4. 最终兜底：硬截断
        compressed = self._hard_trim(text, target_tokens)
        return compressed, CompressionResult(
            compressed_input=compressed,
            original_tokens=original_tokens,
            compressed_tokens=TokenEstimator.estimate(compressed),
            strategy="hard_trim",
            quality_flag="degraded",
            notes=["final fallback: hard truncation"],
        )

    def _is_conversation_content(self, text: str) -> bool:
        """判断是否为对话内容（消息列表格式）。"""
        import json

        try:
            data = json.loads(text)
            return isinstance(data, list) and len(data) > 0 and all(isinstance(m, dict) and "role" in m for m in data)
        except (json.JSONDecodeError, TypeError):
            return False

    def _is_code_content(self, text: str) -> bool:
        """判断是否为代码内容。"""
        code_indicators = sum(1 for c in text if c in "{};()[]=<>")
        return code_indicators > len(text) * 0.05 if text else False

    def _try_role_context_compressor(self, text: str, target_tokens: int) -> CompressionResult | None:
        """尝试使用 RoleContextCompressor（通过端口接口避免循环导入）。"""
        try:
            # Use injected port if available
            if self._compressor_port is not None:
                return self._use_compressor_port(text, target_tokens)

            # Fallback: lazy import to avoid circular dependency
            return self._try_lazy_import_compressor(text, target_tokens)

        except (ValueError, TypeError, RuntimeError) as exc:
            logger.debug(
                "CompressionRouter._try_role_context_compressor: compression failed: %s",
                exc,
            )
            return None

    def _use_compressor_port(self, text: str, target_tokens: int) -> CompressionResult | None:
        """Use the injected compressor port."""
        from polaris.kernelone.llm.ports import ContextIdentity

        if self._compressor_port is None:
            return None

        role_name = str(self.role or "ai_platform").strip() or "ai_platform"
        messages = [{"role": "user", "content": text}]

        identity = ContextIdentity(
            role_id=f"{role_name}_budget_{id(self)}",
            role_type=role_name,
            goal="keep prompt within model context limit",
            scope=cast("tuple[str, ...]", ()),
            current_phase="token_budget",
            metadata={"source": "token_budget_manager"},
        )

        compressed_messages, snapshot = self._compressor_port.compact_if_needed(
            messages,
            identity,
            force_compact=True,
            focus="Preserve constraints, acceptance criteria, and latest user intent",
        )

        if snapshot is None:
            return None

        compressed_text = "\n\n".join(
            str(m.get("content") or "") for m in compressed_messages if str(m.get("content") or "").strip()
        ).strip()

        if not compressed_text:
            return None

        return CompressionResult(
            compressed_input=compressed_text,
            original_tokens=snapshot.original_tokens,
            compressed_tokens=snapshot.compressed_tokens,
            strategy="role_context_compressor",
            notes=[f"method={snapshot.method}"] if snapshot.method else [],
        )

    def _try_lazy_import_compressor(self, text: str, target_tokens: int) -> CompressionResult | None:
        """Lazy import fallback for backward compatibility."""
        from polaris.kernelone.context import (
            RoleContextCompressor,
            RoleContextIdentity,
        )

        role_name = str(self.role or "ai_platform").strip() or "ai_platform"
        workspace_path = str(self.workspace or ".")

        messages = [{"role": "user", "content": text}]

        compressor = RoleContextCompressor(
            workspace=workspace_path,
            role_name=role_name,
            config={
                "token_threshold": target_tokens,
                "micro_compact_keep": 1,
            },
        )
        identity = RoleContextIdentity.from_role_state(
            role_name=role_name,
            goal="keep prompt within model context limit",
            scope=[],
            metadata={"source": "token_budget_manager"},
        )
        compressed_messages, snapshot = compressor.compact_if_needed(
            messages,
            identity,
            force_compact=True,
            focus="Preserve constraints, acceptance criteria, and latest user intent",
        )

        if not snapshot:
            return None

        compressed_text = "\n\n".join(
            str(m.get("content") or "") for m in compressed_messages if str(m.get("content") or "").strip()
        ).strip()

        if not compressed_text:
            return None

        return CompressionResult(
            compressed_input=compressed_text,
            original_tokens=snapshot.original_tokens,
            compressed_tokens=snapshot.compressed_tokens,
            strategy="role_context_compressor",
            notes=[f"method={snapshot.method}"] if snapshot.method else [],
        )

    def _compress_code(self, text: str, target_tokens: int) -> str:
        """代码专用压缩：删除 import 块和注释块。"""
        lines = text.splitlines()
        compact_lines: list[str] = []
        blank_run = 0
        import_run = 0
        comment_run = 0
        suppressed_imports = 0
        suppressed_comments = 0

        def is_import_line(line: str) -> bool:
            stripped = line.strip()
            return stripped.startswith(("import ", "from ")) and "(" not in stripped

        def is_comment_line(line: str) -> bool:
            stripped = line.strip()
            return stripped.startswith(("#", "//", "/*", "*", "*/", '"""', "'''"))

        for line in lines:
            stripped = line.strip()

            if is_import_line(line):
                import_run += 1
                if import_run > 12:
                    suppressed_imports += 1
                    continue
            else:
                import_run = 0

            if is_comment_line(line):
                comment_run += 1
                if comment_run > 8:
                    suppressed_comments += 1
                    continue
            else:
                comment_run = 0

            if not stripped:
                blank_run += 1
                if blank_run > 1:
                    continue
                compact_lines.append("")
            else:
                blank_run = 0
                compact_lines.append(line)

        if suppressed_imports > 0:
            compact_lines.append(f"# ... [omitted {suppressed_imports} import lines]")
        if suppressed_comments > 0:
            compact_lines.append(f"# ... [omitted {suppressed_comments} comment lines]")

        result = "\n".join(compact_lines).strip("\n")

        # 如果还是超，再应用硬截断
        if TokenEstimator.estimate(result) > target_tokens:
            return self._hard_trim(result, target_tokens)

        return result

    def _compress_by_lines(self, text: str, target_tokens: int) -> str:
        """通用行压缩：保留头部和尾部。"""
        lines = [line for line in text.splitlines() if line.strip()]
        if not lines or len(lines) < 24:
            return self._hard_trim(text, target_tokens)

        keep_total = max(12, int(len(lines) * 0.35))
        keep_head = max(6, int(keep_total * 0.7))
        keep_tail = max(4, keep_total - keep_head)

        head = lines[:keep_head]
        tail = lines[-keep_tail:] if keep_tail > 0 else []
        dropped = max(0, len(lines) - len(head) - len(tail))

        compacted = "\n".join(
            [
                *head,
                "",
                f"[... {dropped} lines compressed by TokenBudgetManager ...]",
                "",
                *tail,
            ]
        )

        if TokenEstimator.estimate(compacted) > target_tokens:
            return self._hard_trim(compacted, target_tokens)

        return compacted

    def _hard_trim(self, text: str, target_tokens: int) -> str:
        """最终兜底：硬截断。"""
        if target_tokens <= 0:
            return ""

        # 估算每个 token 约 4 字符
        max_chars = max(64, int(target_tokens * 4))

        if len(text) <= max_chars:
            return text

        marker = "\n\n[... context compressed to fit model limit ...]\n\n"
        usable = max(16, max_chars - len(marker))
        head_chars = int(usable * 0.7)
        tail_chars = usable - head_chars

        return text[:head_chars] + marker + text[-tail_chars:]


class TokenBudgetManager:
    """Token 预算管理器 - 纯预算决策器。

    职责：
    1. 计算可用预算（考虑安全边距和输出预留）
    2. 检测是否超限
    3. 路由压缩请求
    4. 最终兜底截断

    不直接实现压缩逻辑，而是通过 CompressionRouter 委派。
    """

    def __init__(
        self,
        safety_margin_ratio: float = 0.12,
        min_output_tokens: int = 256,
        min_prompt_budget_tokens: int = 256,
    ) -> None:
        self.safety_margin_ratio = max(0.01, min(0.4, float(safety_margin_ratio)))
        self.min_output_tokens = max(32, int(min_output_tokens))
        self.min_prompt_budget_tokens = max(64, int(min_prompt_budget_tokens))

    def enforce(
        self,
        input_text: str,
        model_spec: ModelSpec,
        requested_output_tokens: int | None = None,
        workspace: str | None = None,
        role: str | None = None,
        *,
        content_type: str = "general",
        compression_history: list[str] | None = None,
    ) -> TokenBudgetDecision:
        """执行预算检查，必要时触发压缩。

        Args:
            input_text: 输入文本
            model_spec: 模型规格
            requested_output_tokens: 请求的输出 token 数
            workspace: 工作区路径
            role: 角色名称
            content_type: 内容类型 (general/code/conversation/cjk)
            compression_history: 已应用的压缩策略列表（防重复）

        Returns:
            TokenBudgetDecision
        """
        text = str(input_text or "")

        # 检查是否已被压缩过
        if compression_history:
            # 如果已经被智能压缩过，直接做硬截断检查
            return self._enforce_with_budget(
                text,
                model_spec,
                requested_output_tokens,
                already_compressed=True,
                content_type=content_type,
            )

        return self._enforce_with_budget(
            text,
            model_spec,
            requested_output_tokens,
            already_compressed=False,
            workspace=workspace,
            role=role,
            content_type=content_type,
            compression_history=compression_history or [],
        )

    def _enforce_with_budget(
        self,
        text: str,
        model_spec: ModelSpec,
        requested_output_tokens: int | None,
        already_compressed: bool,
        workspace: str | None = None,
        role: str | None = None,
        content_type: str = "general",
        compression_history: list[str] | None = None,
    ) -> TokenBudgetDecision:
        """内部预算执行逻辑。"""

        # 1. 计算预算
        max_context_tokens = max(512, int(model_spec.max_context_tokens or 0))
        reserve_output_tokens = self._resolve_reserved_output_tokens(
            model_spec=model_spec,
            requested_output_tokens=requested_output_tokens,
            max_context_tokens=max_context_tokens,
        )
        # Keep a large safety margin for capable models, but never let the
        # margin consume the prompt budget on small/local models.
        preferred_safety_margin = max(2048, int(max_context_tokens * 0.05))
        max_safe_margin = max(0, max_context_tokens - reserve_output_tokens - self.min_prompt_budget_tokens)
        safety_margin_tokens = min(preferred_safety_margin, max_safe_margin)
        hard_available = max_context_tokens - reserve_output_tokens - safety_margin_tokens
        allowed_prompt_tokens = max(1, min(hard_available, max_context_tokens - reserve_output_tokens))

        # 2. 估算当前输入
        tokenizer_hint = model_spec.tokenizer if model_spec.tokenizer != "char_estimate" else None
        requested_prompt_tokens = TokenEstimator.estimate(
            text, content_type=content_type, tokenizer_hint=tokenizer_hint
        )

        # 3. 在预算内？直接通过
        if requested_prompt_tokens <= allowed_prompt_tokens:
            return TokenBudgetDecision(
                allowed=True,
                max_context_tokens=max_context_tokens,
                allowed_prompt_tokens=allowed_prompt_tokens,
                requested_prompt_tokens=requested_prompt_tokens,
                reserved_output_tokens=reserve_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                compression_applied=False,
            )

        # 4. 已压缩过但仍超？硬截断
        if already_compressed:
            hard_trimmed = self._hard_trim(text, allowed_prompt_tokens)
            hard_trimmed_tokens = TokenEstimator.estimate(hard_trimmed, content_type=content_type)

            return TokenBudgetDecision(
                allowed=True,
                max_context_tokens=max_context_tokens,
                allowed_prompt_tokens=allowed_prompt_tokens,
                requested_prompt_tokens=requested_prompt_tokens,
                reserved_output_tokens=reserve_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                compression_applied=True,
                compression=CompressionResult(
                    compressed_input=hard_trimmed,
                    original_tokens=requested_prompt_tokens,
                    compressed_tokens=hard_trimmed_tokens,
                    strategy="hard_trim_post_compression",
                    quality_flag="degraded",
                    notes=["input exceeded budget even after prior compression", "applied hard trim fallback"],
                ),
            )

        # 5. 需要压缩：路由到最佳压缩器
        router = CompressionRouter(workspace=workspace, role=role)
        compressed_text, compression_result = router.route_and_compress(
            text,
            allowed_prompt_tokens,
            content_type=content_type,
            compression_history=compression_history,
        )

        compressed_tokens = TokenEstimator.estimate(compressed_text, content_type=content_type)

        # 更新结果中的 token 数
        compression_result.original_tokens = requested_prompt_tokens
        compression_result.compressed_tokens = compressed_tokens
        compression_result.drop_ratio = self._drop_ratio(requested_prompt_tokens, compressed_tokens)
        if compression_result.quality_flag != "degraded":
            compression_result.quality_flag = self._quality_from_ratio(compression_result.drop_ratio)

        # 6. 压缩后仍超？拒绝
        if compressed_tokens > allowed_prompt_tokens:
            return TokenBudgetDecision(
                allowed=False,
                max_context_tokens=max_context_tokens,
                allowed_prompt_tokens=allowed_prompt_tokens,
                requested_prompt_tokens=requested_prompt_tokens,
                reserved_output_tokens=reserve_output_tokens,
                safety_margin_tokens=safety_margin_tokens,
                compression_applied=True,
                compression=compression_result,
                error=(
                    f"Prompt exceeds model context window even after compression. "
                    f"requested={requested_prompt_tokens}, allowed={allowed_prompt_tokens}, "
                    f"compressed={compressed_tokens}"
                ),
            )

        return TokenBudgetDecision(
            allowed=True,
            max_context_tokens=max_context_tokens,
            allowed_prompt_tokens=allowed_prompt_tokens,
            requested_prompt_tokens=requested_prompt_tokens,
            reserved_output_tokens=reserve_output_tokens,
            safety_margin_tokens=safety_margin_tokens,
            compression_applied=True,
            compression=compression_result,
        )

    def _resolve_reserved_output_tokens(
        self,
        *,
        model_spec: ModelSpec,
        requested_output_tokens: int | None,
        max_context_tokens: int,
    ) -> int:
        """解析预留的输出 token 数。"""
        requested = int(requested_output_tokens or 0)
        if requested <= 0:
            requested = int(model_spec.max_output_tokens or 0)
        if requested <= 0:
            requested = self.min_output_tokens
        requested = max(self.min_output_tokens, requested)
        return min(requested, max_context_tokens - 1)

    def _hard_trim(self, text: str, target_tokens: int) -> str:
        """最终兜底：硬截断。"""
        if target_tokens <= 0:
            return ""

        max_chars = max(64, int(target_tokens * 4))

        if len(text) <= max_chars:
            return text

        marker = "\n\n[... context compressed to fit model limit ...]\n\n"
        usable = max(16, max_chars - len(marker))
        head_chars = int(usable * 0.7)
        tail_chars = usable - head_chars

        return text[:head_chars] + marker + text[-tail_chars:]

    def _drop_ratio(self, original_tokens: int, compressed_tokens: int) -> float:
        """计算压缩率。"""
        if original_tokens <= 0:
            return 0.0
        return max(0.0, min(1.0, 1.0 - (compressed_tokens / original_tokens)))

    def _quality_from_ratio(self, ratio: float) -> str:
        """根据压缩率评估质量。"""
        if ratio >= 0.7:
            return "degraded"
        if ratio >= 0.45:
            return "warning"
        return "ok"


__all__ = ["CompressionRouter", "TokenBudgetManager", "TokenEstimator"]
