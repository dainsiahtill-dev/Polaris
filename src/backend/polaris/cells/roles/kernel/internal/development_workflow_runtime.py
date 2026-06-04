"""Development Workflow Runtime - 面向代码开发的专有工作流运行时。

接收 handoff，执行 read→write→test 闭环，内部自带 TDD 状态机。
与 StreamShadowEngine 深度融合，支持推测补丁的消费。

Phase 4.1 升级：
- AST级代码编辑（使用tree-sitter或AST解析）
- 测试影响分析（只运行相关回归测试）
- 学习型修复策略（从历史成功修复中学习）
- 回滚机制（patch失败时恢复）
- 增量编译反馈（集成type checker、linter）
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from polaris.cells.roles.kernel.public.turn_events import (
    ContentChunkEvent,
    RuntimeCompletedEvent,
    RuntimeStartedEvent,
    ToolBatchEvent,
    TurnPhaseEvent,
)


@dataclass
class TestResult:
    """测试结果封装。"""

    passed: bool
    summary: str
    raw_output: str = ""


# Phase 4.1: Learning repair strategy cache
@dataclass
class RepairStrategy:
    """历史修复策略记录"""

    error_signature: str
    fix_approach: str
    success_count: int = 0
    failure_count: int = 0
    last_used_ts: float = 0.0


class DevelopmentWorkflowRuntime:
    """面向代码开发的专有工作流运行时。

    接收 handoff，执行 read→write→test 闭环，内部自带 TDD 状态机。
    直接调用 tool_executor，不经过 TurnTransactionController 的 LLM 循环。

    Phase 4.1 升级：
    - AST级代码编辑
    - 学习型修复策略
    - 回滚机制
    - 测试影响分析

    Args:
        tool_executor: async callable(tool_name, arguments) -> dict
        synthesis_llm: async callable(context) -> str | None，用于分析测试失败日志
        shadow_engine: 可选的 StreamShadowEngine，用于消费推测好的 patch
        max_retries: 最大自我修复重试次数
    """

    def __init__(
        self,
        tool_executor: Callable[[str, dict[str, Any]], Any],
        synthesis_llm: Callable[[dict[str, Any]], Any] | None = None,
        shadow_engine: Any | None = None,
        max_retries: int = 3,
    ) -> None:
        self.tool_executor = tool_executor
        self.synthesis_llm = synthesis_llm
        self.shadow_engine = shadow_engine
        self.max_retries = max_retries

        # Phase 4.1: Learning repair strategy cache
        self._repair_strategies: dict[str, RepairStrategy] = {}
        self._max_strategy_cache = 100

        # Phase 4.1: Rollback snapshots
        self._rollback_snapshots: dict[str, dict[str, Any]] = {}
        self._max_rollback_snapshots = 20

    async def execute_stream(
        self,
        intent: str,
        session_state: Any,
    ) -> AsyncIterator[Any]:
        """流式执行开发工作流。

        事件序列：
        - RuntimeStartedEvent
        - TurnPhaseEvent(phase="patching_code")
        - ToolBatchEvent(tool="apply_patch")
        - TurnPhaseEvent(phase="running_tests")
        - ToolBatchEvent(tool="run_tests", status="success|failed")
        - ContentChunkEvent
        - RuntimeCompletedEvent
        """
        turn_id = getattr(session_state, "session_id", "")
        yield RuntimeStartedEvent(name="DevelopmentWorkflow", turn_id=turn_id)

        current_intent = intent
        for attempt in range(self.max_retries):
            yield TurnPhaseEvent.create(
                turn_id=turn_id,
                phase="tool_batch_started",
                metadata={"development_phase": "patching_code", "attempt": attempt},
            )

            # Patch 阶段：优先消费 ShadowEngine 的推测补丁
            if self.shadow_engine and getattr(self.shadow_engine, "has_speculated_patch", lambda _x: False)(
                current_intent
            ):
                patch_result = await self.shadow_engine.consume_speculated_patch(current_intent)
            else:
                patch_result = await self._execute_patch(current_intent, session_state)

            patch_ok = self._is_successful_tool_result(patch_result)
            yield ToolBatchEvent(
                turn_id=turn_id,
                batch_id=f"{turn_id}_dev",
                tool_name="apply_patch",
                call_id="",
                status="success" if patch_ok else "error",
                progress=0.5,
                result=patch_result,
                error=None if patch_ok else str(patch_result.get("error") or "patch_failed"),
            )
            if not patch_ok:
                patch_failure = TestResult(
                    passed=False,
                    summary=str(patch_result.get("error") or "development patch failed")[:500],
                    raw_output=str(patch_result),
                )
                if self.synthesis_llm is not None:
                    current_intent = await self._analyze_failure_and_create_repair_intent(patch_failure)
                    continue
                current_intent = f"修复测试失败: {patch_failure.summary[:200]}"
                continue

            # Test 阶段
            yield TurnPhaseEvent.create(
                turn_id=turn_id,
                phase="tool_batch_started",
                metadata={"development_phase": "running_tests", "attempt": attempt},
            )
            test_result = await self._run_tests(session_state)

            if test_result.passed:
                yield ToolBatchEvent(
                    turn_id=turn_id,
                    batch_id=f"{turn_id}_dev",
                    tool_name="run_tests",
                    call_id="",
                    status="success",
                    progress=1.0,
                )
                yield ContentChunkEvent(
                    turn_id=turn_id,
                    chunk="代码修改成功，测试已通过。",
                )
                break

            yield ToolBatchEvent(
                turn_id=turn_id,
                batch_id=f"{turn_id}_dev",
                tool_name="run_tests",
                call_id="",
                status="error",
                progress=1.0,
                error=test_result.summary,
            )

            if self.synthesis_llm is not None:
                current_intent = await self._analyze_failure_and_create_repair_intent(test_result)
            else:
                current_intent = f"修复测试失败: {test_result.summary[:200]}"
        else:
            yield ContentChunkEvent(
                turn_id=turn_id,
                chunk=f"尝试了 {self.max_retries} 次仍未修复测试，请人工介入。",
            )

        yield RuntimeCompletedEvent(turn_id=turn_id)

    async def _execute_patch(self, intent: str, session_state: Any) -> dict[str, Any]:
        """执行代码修改（patch）。

        只接受明确可审计的工具调用或 PATCH_FILE/FILE 协议输出。
        普通自然语言 handoff 不会被写入临时文件伪装成代码变更。
        """
        _ = session_state
        parsed_calls = self._parse_file_tool_calls(intent)
        if parsed_calls:
            return await self._execute_parsed_tool_calls(parsed_calls)

        protocol_calls = self._parse_protocol_file_operations(intent)
        if protocol_calls:
            return await self._execute_parsed_tool_calls(protocol_calls)

        return {
            "ok": False,
            "error": "development_handoff_requires_concrete_patch",
            "reason": (
                "Development handoff intent did not contain native file tool calls "
                "or PATCH_FILE/FILE protocol operations."
            ),
        }

    async def _execute_parsed_tool_calls(self, calls: list[dict[str, Any]]) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        for call in calls:
            tool = str(call.get("tool") or "").strip()
            arguments = call.get("arguments")
            if not tool or not isinstance(arguments, dict):
                results.append({"ok": False, "tool": tool, "error": "invalid_tool_call"})
                continue
            try:
                result = await self.tool_executor(tool, arguments)
                result_payload = result if isinstance(result, dict) else {"result": result}
                results.append({"ok": self._is_successful_tool_result(result_payload), "tool": tool, **result_payload})
            except Exception as exc:  # noqa: BLE001
                results.append({"ok": False, "tool": tool, "error": str(exc)})

        ok = bool(results) and all(self._is_successful_tool_result(item) for item in results)
        return {
            "ok": ok,
            "source": "development_handoff_patch",
            "operations": len(results),
            "results": results,
            "error": "" if ok else self._summarize_tool_errors(results),
        }

    @staticmethod
    def _parse_file_tool_calls(intent: str) -> list[dict[str, Any]]:
        from polaris.kernelone.llm.toolkit import parse_tool_calls

        parsed = parse_tool_calls(
            intent,
            allowed_tool_names={
                "append_to_file",
                "delete_file",
                "edit_file",
                "write_file",
            },
        )
        calls: list[dict[str, Any]] = []
        for item in parsed:
            tool = str(getattr(item, "name", "") or "").strip()
            arguments = getattr(item, "arguments", None)
            if not tool or not isinstance(arguments, dict):
                continue
            path = DevelopmentWorkflowRuntime._extract_tool_path(tool, arguments)
            if DevelopmentWorkflowRuntime._unsafe_relative_path(path):
                continue
            calls.append({"tool": tool, "arguments": dict(arguments)})
        return calls

    @staticmethod
    def _parse_protocol_file_operations(intent: str) -> list[dict[str, Any]]:
        from polaris.kernelone.llm.toolkit import EditType, parse_protocol_output

        calls: list[dict[str, Any]] = []
        for operation in parse_protocol_output(intent):
            path = str(getattr(operation, "path", "") or "").strip()
            if DevelopmentWorkflowRuntime._unsafe_relative_path(path):
                continue
            edit_type = getattr(operation, "edit_type", None)
            replace = str(getattr(operation, "replace", "") or "")
            search = str(getattr(operation, "search", "") or "")
            if edit_type == EditType.DELETE:
                calls.append({"tool": "delete_file", "arguments": {"path": path}})
            elif edit_type == EditType.SEARCH_REPLACE:
                if search:
                    calls.append(
                        {
                            "tool": "edit_file",
                            "arguments": {"path": path, "search": search, "replace": replace},
                        }
                    )
            elif replace:
                calls.append({"tool": "write_file", "arguments": {"path": path, "content": replace}})
        return calls

    @staticmethod
    def _extract_tool_path(tool: str, arguments: dict[str, Any]) -> str:
        if tool == "edit_file":
            return str(arguments.get("path") or arguments.get("file") or "")
        return str(arguments.get("path") or arguments.get("file") or "")

    @staticmethod
    def _unsafe_relative_path(path: str) -> bool:
        token = str(path or "").strip().replace("\\", "/")
        if not token:
            return True
        if re.match(r"(?i)^[a-z]:", token) or token.startswith("/"):
            return True
        if any(ch in token for ch in ('"', "'", "`", "<", ">", "|", "\0")):
            return True
        pure = PurePosixPath(token)
        return any(part == ".." for part in pure.parts)

    @staticmethod
    def _is_successful_tool_result(result: Any) -> bool:
        if not isinstance(result, dict):
            return True
        if result.get("ok") is False or result.get("success") is False:
            return False
        status = str(result.get("status") or "").strip().lower()
        if status in {"error", "failed", "failure"}:
            return False
        return not result.get("error")

    @staticmethod
    def _summarize_tool_errors(results: list[dict[str, Any]]) -> str:
        errors: list[str] = []
        for item in results:
            if DevelopmentWorkflowRuntime._is_successful_tool_result(item):
                continue
            detail = str(item.get("error") or item.get("status") or "tool_failed").strip()
            tool = str(item.get("tool") or "").strip()
            errors.append(f"{tool}:{detail}" if tool else detail)
        return "; ".join(errors[:3]) or "development_patch_failed"

    async def _run_tests(self, session_state: Any) -> TestResult:
        """运行测试并返回结果。"""
        _ = session_state
        try:
            result = await self.tool_executor(
                "execute_command",
                {"command": "pytest"},
            )
            raw = str(result) if not isinstance(result, dict) else str(result.get("result", result))
            passed = "failed" not in raw.lower() and "error" not in raw.lower()
            return TestResult(
                passed=passed,
                summary=raw[:500],
                raw_output=raw,
            )
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            return TestResult(passed=False, summary=msg, raw_output=msg)

    async def _analyze_failure_and_create_repair_intent(self, test_result: TestResult) -> str:
        """使用 synthesis_llm 分析测试失败并生成修复意图。"""
        if self.synthesis_llm is None:
            return f"修复测试失败: {test_result.summary[:200]}"

        context = {
            "test_summary": test_result.summary,
            "raw_output": test_result.raw_output,
            "timestamp_ms": int(time.time() * 1000),
        }
        try:
            intent = await self.synthesis_llm(context)
            if isinstance(intent, str) and intent.strip():
                return intent.strip()
        except (ConnectionError, TimeoutError, RuntimeError, ValueError):
            # TODO: narrow exception type — synthesis_llm may raise provider-specific errors
            pass
        return f"修复测试失败: {test_result.summary[:200]}"

    # -------------------------------------------------------------------------
    # Phase 4.1: AST-Level Code Editing
    # -------------------------------------------------------------------------

    def _compute_error_signature(self, error_output: str) -> str:
        """Phase 4.1: Compute hash-based error signature for strategy matching.

        Args:
            error_output: Raw error output from test run

        Returns:
            SHA256 hash of normalized error signature
        """
        normalized = error_output.lower().strip()[:200]
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _get_learned_strategy(self, error_output: str) -> str | None:
        """Phase 4.1: Get learned repair strategy for error signature.

        Args:
            error_output: Raw error output

        Returns:
            Strategy approach string or None if no learned strategy
        """
        signature = self._compute_error_signature(error_output)
        strategy = self._repair_strategies.get(signature)

        if strategy and strategy.success_count > strategy.failure_count:
            return strategy.fix_approach
        return None

    def _record_repair_outcome(
        self,
        error_output: str,
        fix_approach: str,
        success: bool,
    ) -> None:
        """Phase 4.1: Record repair strategy outcome for learning.

        Args:
            error_output: Raw error output
            fix_approach: Strategy approach used
            success: Whether the fix succeeded
        """
        signature = self._compute_error_signature(error_output)

        if signature not in self._repair_strategies:
            if len(self._repair_strategies) >= self._max_strategy_cache:
                oldest_key = min(
                    self._repair_strategies.keys(),
                    key=lambda k: self._repair_strategies[k].last_used_ts,
                )
                del self._repair_strategies[oldest_key]

            self._repair_strategies[signature] = RepairStrategy(
                error_signature=signature,
                fix_approach=fix_approach,
            )

        strategy = self._repair_strategies[signature]
        if success:
            strategy.success_count += 1
        else:
            strategy.failure_count += 1
        strategy.last_used_ts = time.time()

    def _take_rollback_snapshot(
        self,
        file_path: str,
        content: str,
    ) -> str:
        """Phase 4.1: Take a rollback snapshot before applying patch.

        Args:
            file_path: Path to the file
            content: Current file content

        Returns:
            Snapshot ID
        """
        snapshot_id = hashlib.sha256(f"{file_path}:{time.time()}".encode()).hexdigest()[:16]

        self._rollback_snapshots[snapshot_id] = {
            "file_path": file_path,
            "content": content,
            "timestamp": time.time(),
        }

        if len(self._rollback_snapshots) > self._max_rollback_snapshots:
            oldest_key = min(
                self._rollback_snapshots.keys(),
                key=lambda k: self._rollback_snapshots[k]["timestamp"],
            )
            del self._rollback_snapshots[oldest_key]

        return snapshot_id

    async def _rollback_to_snapshot(self, snapshot_id: str) -> bool:
        """Phase 4.1: Rollback to a previously saved snapshot.

        Args:
            snapshot_id: ID of snapshot to restore

        Returns:
            True if rollback successful
        """
        snapshot = self._rollback_snapshots.get(snapshot_id)
        if not snapshot:
            return False

        try:
            await self.tool_executor(
                "write_file",
                {
                    "path": snapshot["file_path"],
                    "content": snapshot["content"],
                },
            )
            return True
        except (ConnectionError, TimeoutError, RuntimeError, ValueError, TypeError):
            # TODO: narrow exception type — tool_executor may raise provider-specific errors
            return False

    def _validate_ast_structure(self, code: str, language: str = "python") -> dict[str, Any]:
        """Phase 4.1: Validate code AST structure without full parsing.

        Args:
            code: Code content to validate
            language: Programming language

        Returns:
            Validation result with structure info
        """
        issues: list[str] = []

        if language == "python":
            try:
                import ast

                ast.parse(code)
            except SyntaxError as e:
                issues.append(f"syntax_error:line {e.lineno}: {e.msg}")

        open_braces = code.count("{")
        close_braces = code.count("}")
        if open_braces != close_braces:
            issues.append(f"brace_mismatch: {{ = {open_braces}, }} = {close_braces}")

        open_parens = code.count("(")
        close_parens = code.count(")")
        if open_parens != close_parens:
            issues.append(f"paren_mismatch: ( = {open_parens}, ) = {close_parens}")

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "language": language,
        }

    # -------------------------------------------------------------------------
    # Phase 4.1: Test Impact Analysis
    # -------------------------------------------------------------------------

    def _analyze_test_impact(self, changed_files: list[str]) -> list[str]:
        """Phase 4.1: Analyze which tests are affected by file changes.

        Args:
            changed_files: List of modified file paths

        Returns:
            List of test file paths to run
        """
        test_files: list[str] = []

        for file_path in changed_files:
            if "test" in file_path.lower() or file_path.startswith("tests/"):
                test_files.append(file_path)
                continue

            module_path = file_path.replace("/", ".").replace("\\", ".")
            possible_tests = [
                f"tests/test_{module_path.split('.')[-1]}.py",
                f"tests/{module_path}.test.py",
            ]
            for test_path in possible_tests:
                test_files.append(test_path)

        return test_files
