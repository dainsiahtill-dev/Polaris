"""Streaming PATCH Block Buffer Manager.

流式 PATCH 块缓冲管理器 - 用于在流式输出过程中检测、缓冲和执行 PATCH 块。

设计原则:
1. 原子性: PATCH 块必须完整才执行，不支持部分执行
2. 缓冲: 等待 PATCH 块完整后再解析
3. 复用: 使用 ProtocolParser.parse() + StrictOperationApplier 执行
4. 日志: 详细日志记录缓冲状态和执行结果

边界条件:
- 不完整的 PATCH 块 (只有 <<<<<<< SEARCH 没有 >>>>>>> REPLACE) 会被缓冲
- 多个 PATCH 块会按顺序处理
- PATCH 块外的文本正常传递给用户
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class PatchBlock:
    """完整的 PATCH 块."""

    path: str
    search: str
    replace: str
    edit_type: str = "search_replace"  # search_replace, full_file, create, delete
    raw_text: str = ""  # 原始文本，用于调试
    start_line: int = 0  # 在原始文本中的起始行号
    end_line: int = 0  # 在原始文本中的结束行号


@dataclass
class PatchExecutionResult:
    """PATCH 执行结果."""

    success: bool
    path: str
    changed: bool = False
    error_code: str = ""
    error_message: str = ""
    old_hash: str = ""
    new_hash: str = ""
    engine: str = "streaming_patch_buffer"  # 标记来源


class StreamingPatchBuffer:
    """流式 PATCH 块缓冲管理器.

    在流式输出过程中:
    1. 检测 PATCH 块开始标记 (<<<<<<< SEARCH)
    2. 缓冲内容直到遇到结束标记 (>>>>>>> REPLACE)
    3. 完整后解析并执行 PATCH 块
    4. 返回可见文本和执行结果

    使用状态机模式:
    - NORMAL: 正常文本，检测 PATCH 开始
    - BUFFERING: 正在缓冲 PATCH 块，等待完整
    - PATCH_COMPLETE: PATCH 块完整，准备执行
    """

    # 状态枚举
    STATE_NORMAL = "normal"
    STATE_BUFFERING = "buffering"
    STATE_PATCH_COMPLETE = "patch_complete"

    # PATCH 标记正则（仅用于状态检测）
    _SEARCH_START_RE = re.compile(r"<<<<<<<\s*SEARCH", re.IGNORECASE)
    _REPLACE_END_RE = re.compile(r">>>>>>>\s*REPLACE", re.IGNORECASE)

    def __init__(self, workspace: str) -> None:
        """初始化缓冲器.

        Args:
            workspace: 工作区路径
        """
        self.workspace = str(workspace)
        self._state = self.STATE_NORMAL
        self._buffer: list[str] = []
        self._complete_blocks: list[PatchBlock] = []
        self._seen_complete_blocks: set[str] = set()
        self._current_search: list[str] = []
        self._current_replace: list[str] = []
        self._search_start_pos = 0

        # 统计
        self._stats = {
            "blocks_buffered": 0,
            "blocks_completed": 0,
            "blocks_executed": 0,
            "bytes_processed": 0,
        }

    @property
    def state(self) -> str:
        """当前状态."""
        return self._state

    @property
    def is_buffering(self) -> bool:
        """是否正在缓冲 PATCH 块."""
        return self._state == self.STATE_BUFFERING

    @property
    def stats(self) -> dict[str, int]:
        """统计信息."""
        return dict(self._stats)

    def feed(self, chunk: str) -> tuple[str, list[PatchBlock]]:
        """处理流式文本块.

        Args:
            chunk: 新接收的文本块

        Returns:
            (可见文本, 完整的 PATCH 块列表)
            可见文本不包含已缓冲的 PATCH 内容
        """
        if not chunk:
            return "", []

        self._stats["bytes_processed"] += len(chunk.encode("utf-8"))

        if self._state == self.STATE_NORMAL:
            return self._feed_normal(chunk)
        elif self._state == self.STATE_BUFFERING:
            return self._feed_buffering(chunk)
        else:
            return chunk, []

    def _feed_normal(self, chunk: str) -> tuple[str, list[PatchBlock]]:
        """正常状态处理: 检测 PATCH 开始标记."""
        search_match = self._SEARCH_START_RE.search(chunk)

        if search_match:
            match_pos = search_match.start()
            logger.info(
                "[StreamingPatchBuffer] >>>>> PATCH 开始标记检测到 (位置: %d)",
                match_pos,
            )

            prefix = chunk[:match_pos]

            # 检查是否有前置文件名 (standalone 格式: filepath\n<<<<<<< SEARCH)
            lines_before = prefix.rstrip().split("\n")
            visible_lines = []
            filename_candidate = None

            for i, line in enumerate(lines_before):
                stripped = line.strip()
                # 检查这行是否像文件路径
                if stripped and "." in stripped and not stripped.startswith("<"):
                    filename_candidate = stripped
                    visible_lines = lines_before[:i]
                else:
                    visible_lines = lines_before[: i + 1]
                    break

            visible_prefix = "\n".join(visible_lines)
            if not visible_prefix.strip():
                visible_prefix = ""

            if filename_candidate:
                logger.debug(
                    "[StreamingPatchBuffer] 检测到文件名候选: %s",
                    filename_candidate,
                )
                buffered_content = filename_candidate + "\n" + chunk[match_pos:]
            else:
                buffered_content = chunk[match_pos:]

            # 检查 PATCH 块是否已经在当前 chunk 中完整
            replace_match = self._REPLACE_END_RE.search(buffered_content)

            if replace_match:
                # PATCH 块完整，立即解析
                # 注意: 完整 PATCH 块需要包含完整上下文 (包括 PATCH_FILE: header)
                # 因此使用整个 chunk 而非 buffered_content
                logger.info(
                    "[StreamingPatchBuffer] PATCH 块在单次输入中完整，立即解析",
                )
                complete_text = prefix + buffered_content
                complete_blocks = self._parse_complete_blocks(complete_text)

                if complete_blocks:
                    for block in complete_blocks:
                        sig = f"{block.path}:{len(block.search)}:{len(block.replace)}"
                        if sig not in self._seen_complete_blocks:
                            self._seen_complete_blocks.add(sig)
                            self._complete_blocks.append(block)
                            self._stats["blocks_completed"] += 1
                            logger.info(
                                "[StreamingPatchBuffer] 解析 PATCH 块: path=%s, search_len=%d, replace_len=%d",
                                block.path,
                                len(block.search),
                                len(block.replace),
                            )

                    # 返回 PATCH 块后的剩余内容
                    remaining = buffered_content[replace_match.end() :]
                    logger.debug(
                        "[StreamingPatchBuffer] 返回 PATCH 后剩余可见文本: %d 字符",
                        len(remaining),
                    )
                    return remaining, complete_blocks
                else:
                    logger.warning(
                        "[StreamingPatchBuffer] PATCH 块解析失败，返回原始可见文本",
                    )
                    return visible_prefix, []

            # PATCH 块不完整，切换到缓冲模式
            logger.debug(
                "[StreamingPatchBuffer] PATCH 块不完整，切换到缓冲模式",
            )
            self._state = self.STATE_BUFFERING
            self._search_start_pos = match_pos
            self._buffer = [buffered_content]
            self._current_search = []
            self._current_replace = []
            self._stats["blocks_buffered"] += 1

            return visible_prefix, []
        else:
            return chunk, []

    def _feed_buffering(self, chunk: str) -> tuple[str, list[PatchBlock]]:
        """缓冲状态处理: 等待 PATCH 块完成."""
        complete_blocks: list[PatchBlock] = []
        self._buffer.append(chunk)

        current_text = "\n".join(self._buffer)
        replace_match = self._REPLACE_END_RE.search(current_text)

        if replace_match:
            logger.info(
                "[StreamingPatchBuffer] >>>>> PATCH 结束标记检测到，准备解析完整块",
            )
            complete_blocks = self._parse_complete_blocks(current_text)

            if complete_blocks:
                for block in complete_blocks:
                    sig = f"{block.path}:{len(block.search)}:{len(block.replace)}"
                    if sig not in self._seen_complete_blocks:
                        self._seen_complete_blocks.add(sig)
                        self._complete_blocks.append(block)
                        self._stats["blocks_completed"] += 1
                        logger.info(
                            "[StreamingPatchBuffer] 解析 PATCH 块: path=%s, search_len=%d, replace_len=%d",
                            block.path,
                            len(block.search),
                            len(block.replace),
                        )

                last_replace_end = replace_match.end()
                remaining = current_text[last_replace_end:]

                logger.debug(
                    "[StreamingPatchBuffer] 解析完成，返回剩余可见文本: %d 字符",
                    len(remaining),
                )

                self._state = self.STATE_NORMAL
                self._buffer = []
                self._current_search = []
                self._current_replace = []

                return remaining, complete_blocks
            else:
                logger.warning(
                    "[StreamingPatchBuffer] PATCH 块解析失败，切换回正常模式",
                )
                self._state = self.STATE_NORMAL
                self._buffer = []
                return "", []

        logger.debug(
            "[StreamingPatchBuffer] PATCH 块未完成，继续缓冲 (当前: %d 字符)",
            len(current_text),
        )
        return "", []

    def _parse_complete_blocks(self, text: str) -> list[PatchBlock]:
        """解析完整的 PATCH 块列表.

        统一委托给 ProtocolParser.parse() 解析，避免代码重复。
        """
        blocks: list[PatchBlock] = []

        try:
            from polaris.kernelone.llm.toolkit.protocol_kernel import (
                EditType,
                FileOperation,
                ProtocolParser,
            )

            # 使用统一 ProtocolParser 解析所有协议方言
            operations = ProtocolParser.parse(text)

            for op in operations:
                if not isinstance(op, FileOperation):
                    continue

                edit_type = "search_replace"
                if op.edit_type == EditType.FULL_FILE:
                    edit_type = "full_file"
                elif op.edit_type == EditType.CREATE:
                    edit_type = "create"
                elif op.edit_type == EditType.DELETE:
                    edit_type = "delete"

                blocks.append(
                    PatchBlock(
                        path=str(op.path or ""),
                        search=str(op.search or ""),
                        replace=str(op.replace or ""),
                        edit_type=edit_type,
                        raw_text="",  # ProtocolParser 不提供原始文本
                        start_line=getattr(op, "source_line", 0) or 0,
                        end_line=0,
                    )
                )

            if not blocks:
                logger.warning(
                    "[StreamingPatchBuffer] ProtocolParser 未解析到任何操作块",
                )

        except ImportError as exc:
            # ProtocolParser 不可用时，记录错误并返回空
            # 注意：不再使用内部回退解析器，保持与 ProtocolParser 行为一致
            logger.error(
                "[StreamingPatchBuffer] ProtocolParser 导入失败: %s, 无法解析 PATCH 块",
                exc,
            )
        except (RuntimeError, ValueError) as exc:
            logger.exception(
                "[StreamingPatchBuffer] 解析 PATCH 块时发生异常: %s",
                exc,
            )

        return blocks

    def flush(self) -> list[PatchBlock]:
        """刷新缓冲器，返回未完成的块（如果有）。"""
        incomplete_warnings: list[PatchBlock] = []

        if self._state == self.STATE_BUFFERING and self._buffer:
            current_text = "\n".join(self._buffer)
            logger.warning(
                "[StreamingPatchBuffer] 流结束，存在不完整的 PATCH 块: 缓冲了 %d 字符",
                len(current_text),
            )
            logger.debug(
                "[StreamingPatchBuffer] 不完整 PATCH 内容预览: %.200s...",
                current_text[:200],
            )

            self._state = self.STATE_NORMAL
            self._buffer = []

        return incomplete_warnings

    def execute_patch_block(self, block: PatchBlock) -> PatchExecutionResult:
        """执行单个 PATCH 块.

        复用 StrictOperationApplier 来执行编辑操作。
        """
        logger.info(
            "[StreamingPatchBuffer] 执行 PATCH 块: path=%s",
            block.path,
        )

        try:
            from polaris.kernelone.llm.toolkit.protocol_kernel import (
                EditType,
                FileOperation,
                StrictOperationApplier,
            )

            # 将 PatchBlock 转换为 FileOperation
            if block.edit_type == "full_file":
                edit_type = EditType.FULL_FILE
            elif block.edit_type == "create":
                edit_type = EditType.CREATE
            elif block.edit_type == "delete":
                edit_type = EditType.DELETE
            else:
                edit_type = EditType.SEARCH_REPLACE

            file_op = FileOperation(
                path=block.path,
                edit_type=edit_type,
                search=block.search,
                replace=block.replace,
                original_format="STREAMING_PATCH",
            )

            # 使用 StrictOperationApplier 执行
            result = StrictOperationApplier.apply(file_op, self.workspace)
            self._stats["blocks_executed"] += 1

            return PatchExecutionResult(
                success=result.success,
                path=block.path,
                changed=result.changed,
                error_code=result.error_code.value if result.error_code else "",
                error_message=result.error_message,
                old_hash=result.old_hash,
                new_hash=result.new_hash,
                engine="strict_operation_applier",
            )

        except ImportError as exc:
            logger.error(
                "[StreamingPatchBuffer] protocol_kernel 不可用: %s",
                exc,
            )
            return PatchExecutionResult(
                success=False,
                path=block.path,
                error_code="IMPORT_ERROR",
                error_message=f"protocol_kernel not available: {exc}",
            )
        except (RuntimeError, ValueError) as exc:
            logger.exception(
                "[StreamingPatchBuffer] PATCH 执行失败: path=%s, error=%s",
                block.path,
                exc,
            )
            return PatchExecutionResult(
                success=False,
                path=block.path,
                error_code="EXECUTION_ERROR",
                error_message=str(exc),
            )

    def reset(self) -> None:
        """重置缓冲器状态."""
        self._state = self.STATE_NORMAL
        self._buffer = []
        self._complete_blocks = []
        self._seen_complete_blocks = set()
        self._current_search = []
        self._current_replace = []
        self._search_start_pos = 0
        logger.debug("[StreamingPatchBuffer] 缓冲器已重置")


def create_streaming_patch_buffer(workspace: str) -> StreamingPatchBuffer:
    """创建流式 PATCH 缓冲器."""
    return StreamingPatchBuffer(workspace=workspace)


__all__ = [
    "PatchBlock",
    "PatchExecutionResult",
    "StreamingPatchBuffer",
    "create_streaming_patch_buffer",
]
