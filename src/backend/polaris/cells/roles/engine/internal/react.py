"""ReAct Engine - 推理-行动引擎

实现 ReAct (Reasoning + Acting) 推理策略：
- 交替进行推理和行动
- 支持 Thought/Action/Observation 循环
- 适合探索性任务
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from .base import (
    BaseEngine,
    EngineBudget,
    EngineContext,
    EngineResult,
    EngineStatus,
    EngineStrategy,
    StepResult,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# ReAct 提示词模板
# ═══════════════════════════════════════════════════════════════════════════

REACT_SYSTEM_PROMPT = """你是一个使用 ReAct (Reasoning + Acting) 策略的推理引擎。

ReAct 策略通过交替进行推理和行动来解决问题：
1. Thought: 分析当前情况，进行推理
2. Action: 决定下一步行动（使用工具或生成答案）
3. Observation: 观察行动结果

输出格式要求：
- 使用 JSON 格式输出你的思考和行动
- 每个步骤必须包含 thought, action, action_input 字段

示例输出：
```json
{
  "thought": "我需要先了解项目结构",
  "action": "list_directory",
  "action_input": {"path": "."}
}
```

当任务完成时，使用 finish 动作：
```json
{
  "thought": "任务已完成",
  "action": "finish",
  "action_input": {"answer": "最终答案"}
}
```
"""


class ReActEngine(BaseEngine):
    """ReAct 推理引擎

    实现推理-行动循环，适合探索性任务。

    特点：
    - 交替进行推理和行动
    - 支持 Thought/Action/Observation 循环
    - 适合代码搜索、调试、探索等任务
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
        max_iterations: int = 10,
    ) -> None:
        """初始化 ReAct 引擎

        Args:
            workspace: 工作区路径
            budget: 预算配置
            max_iterations: 最大迭代次数（建议不超过100）
        """
        super().__init__(workspace, budget)
        # 添加上限约束防止资源耗尽
        self.max_iterations = min(max(1, max_iterations), 100)
        self._history: list[dict[str, Any]] = []
        self._max_history_length = 5

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.REACT

    async def execute(
        self,
        context: EngineContext,
        initial_message: str = "",
    ) -> EngineResult:
        """执行 ReAct 推理

        Args:
            context: 引擎执行上下文
            initial_message: 初始任务描述

        Returns:
            EngineResult: 执行结果
        """
        self._status = EngineStatus.RUNNING
        self._start_time = time.time()
        self._history = []

        try:
            # 主循环
            while self.can_continue():
                # 执行单步
                step_result = await self.step(context)

                # 记录步骤
                self._steps.append(step_result)
                self._current_step += 1

                # 检查是否完成
                if step_result.status == EngineStatus.COMPLETED:
                    return self._create_result(
                        success=True,
                        final_answer=step_result.observation or step_result.thought,
                        termination_reason="task_completed",
                    )

                # 检查是否有错误
                if step_result.error:
                    logger.warning(f"Step {self._current_step} error: {step_result.error}")

                # 限制历史长度
                self._prune_history()

            # 预算耗尽
            return self._create_result(
                success=False,
                final_answer=self._build_partial_answer(),
                termination_reason="budget_exhausted",
            )

        except (RuntimeError, ValueError) as e:
            logger.exception("ReAct engine error")
            return self._create_result(
                success=False,
                final_answer=self._build_partial_answer(),
                error=str(e),
                termination_reason="error",
            )

    async def step(self, context: EngineContext) -> StepResult:
        """执行单步 ReAct 推理

        Args:
            context: 引擎执行上下文

        Returns:
            StepResult: 步骤执行结果
        """
        step_index = self._current_step

        # 构建提示词
        prompt = self._build_prompt(context)

        # 调用 LLM
        llm_response = await self._call_llm(context, prompt)

        # 解析响应
        parsed = self._parse_response(llm_response)

        thought = parsed.get("thought", "")
        action = parsed.get("action", "")
        action_input = parsed.get("action_input", {})

        # 执行行动
        observation = ""
        tool_result = None

        # 初始化错误变量（用于跨分支共享）
        error_var = None

        if action == "finish":
            # 任务完成
            answer = action_input.get("answer", thought)
            return StepResult(
                step_index=step_index,
                status=EngineStatus.COMPLETED,
                thought=thought,
                action=action,
                action_input=action_input,
                observation=answer,
                progress_detected=True,
            )

        elif action and action not in ("finish", "think"):
            # 执行工具调用
            error_occurred = False
            observation = ""
            tool_result = None
            try:
                tool_result = await self._execute_tool(context, action, action_input)
                observation = self._format_observation(action, tool_result)

                # 检查是否有错误
                if isinstance(tool_result, dict) and "error" in tool_result:
                    error_occurred = True

                # 记录工具调用
                self._tool_calls.append(
                    {
                        "tool": action,
                        "input": action_input,
                        "result": tool_result,
                    }
                )
            except (RuntimeError, ValueError) as e:
                observation = f"Error: {e!s}"
                tool_result = {"error": str(e)}
                error_occurred = True
                error_var = str(e)
        else:
            # 其他情况（如 action 为空）
            observation = ""
            tool_result = None
            error_occurred = False

        # 更新历史
        self._history.append(
            {
                "thought": thought,
                "action": action,
                "action_input": action_input,
                "observation": observation,
            }
        )

        # 判断是否有进展：成功执行且无错误才算有进展
        progress_detected = bool(observation) and not error_occurred

        return StepResult(
            step_index=step_index,
            status=EngineStatus.RUNNING,
            thought=thought,
            action=action,
            action_input=action_input,
            observation=observation,
            tool_result=tool_result,
            error=error_var,
            progress_detected=progress_detected,
        )

    def can_continue(self) -> bool:
        """检查是否继续执行"""
        if self._status == EngineStatus.COMPLETED:
            return False
        if self._status == EngineStatus.FAILED:
            return False
        if self._current_step >= self.max_iterations:
            return False
        return self._check_budget()

    def _build_prompt(self, context: EngineContext) -> str:
        """构建 ReAct 提示词"""
        # 添加历史信息
        history_section = ""
        if self._history:
            history_items = []
            for h in self._history[-self._max_history_length :]:
                # 安全访问历史记录字段
                history_items.append(
                    f"Thought: {h.get('thought', '')}\n"
                    f"Action: {h.get('action', '')}\n"
                    f"Observation: {h.get('observation', '')}"
                )
            history_section = "\n\n".join(history_items) + "\n\n"

        # 构建完整提示词
        prompt = f"""{REACT_SYSTEM_PROMPT}

## 当前任务
{context.task}

## 历史步骤
{history_section}请给出你的下一步推理和行动（JSON 格式）：
"""
        return prompt

    def _parse_response(self, response: str) -> dict[str, Any]:
        """解析 LLM 响应"""
        # 方案1: 尝试解析整个响应为 JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            logger.debug("react: primary JSON parse failed (trying fallbacks): %s", exc)

        # 方案2: 使用括号平衡算法提取 JSON
        try:
            parsed = self._extract_balanced_json(response)
            if parsed:
                return parsed
        except (RuntimeError, ValueError):
            logger.debug("react: balanced JSON extraction failed", exc_info=True)

        # 方案3: 查找 JSON 块（支持嵌套）
        try:
            json_match = self._find_json_object(response)
            if json_match:
                return json.loads(json_match)
        except json.JSONDecodeError as exc:
            logger.debug("react: json object slice parse failed: %s", exc)

        # 方案4: 降级解析
        thought_match = re.search(r'"thought"\s*:\s*"([^"]*)"', response)
        action_match = re.search(r'"action"\s*:\s*"([^"]*)"', response)
        input_match = re.search(r'"action_input"\s*:\s*(\{.*?\})', response, re.DOTALL)

        result = {}
        if thought_match:
            result["thought"] = thought_match.group(1)
        if action_match:
            result["action"] = action_match.group(1)
        if input_match:
            try:
                result["action_input"] = json.loads(input_match.group(1))
            except (RuntimeError, ValueError):
                logger.debug("react: action_input JSON parse failed; using empty dict", exc_info=True)
                result["action_input"] = {}

        if not result:
            # 默认响应
            result = {
                "thought": response[:100],
                "action": "finish",
                "action_input": {"answer": response},
            }

        return result

    def _find_json_object(self, text: str) -> str | None:
        """查找 JSON 对象（支持嵌套）"""
        # 找到第一个 { 和最后一个 }
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _extract_balanced_json(self, text: str) -> dict[str, Any] | None:
        """使用括号平衡算法提取 JSON"""
        # 优先尝试对象（因为任务描述通常是 JSON 对象而非裸数组）
        start = text.find("{")
        if start != -1:
            # 对象
            end = self._find_matching_bracket(text, start, "{", "}")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    logger.debug("react: balanced object JSON decode failed: %s", exc)

        start = text.find("[")
        if start != -1:
            # 数组
            end = self._find_matching_bracket(text, start, "[", "]")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    logger.debug("react: balanced array JSON decode failed: %s", exc)

        return None

    def _find_matching_bracket(self, text: str, start: int, open_char: str, close_char: str) -> int:
        """找到匹配的括号位置"""
        count = 1
        i = start + 1
        in_string = False
        escape = False

        while i < len(text) and count > 0:
            char = text[i]

            if escape:
                escape = False
                i += 1
                continue

            if char == "\\":
                escape = True
                i += 1
                continue

            if char == '"':
                in_string = not in_string
                i += 1
                continue

            if not in_string:
                if char == open_char:
                    count += 1
                elif char == close_char:
                    count -= 1

            i += 1

        return i - 1 if count == 0 else -1

    async def _execute_tool(
        self,
        context: EngineContext,
        tool_name: str,
        tool_args: dict[str, Any],
    ) -> dict[str, Any]:
        """执行工具调用"""
        if context.tool_gateway:
            # 使用工具网关
            result = await context.tool_gateway.execute(tool_name, tool_args)
            return result
        else:
            # 模拟工具执行（用于测试）
            return {"success": True, "message": f"Simulated: {tool_name}"}

    def _format_observation(
        self,
        action: str,
        tool_result: dict[str, Any],
    ) -> str:
        """格式化观察结果"""
        if not tool_result:
            return "No result"

        if isinstance(tool_result, dict):
            if "error" in tool_result:
                return f"Error: {tool_result['error']}"
            if "content" in tool_result:
                return str(tool_result["content"])[:500]
            if "output" in tool_result:
                return str(tool_result["output"])[:500]
            return str(tool_result)[:500]

        return str(tool_result)[:500]

    def _prune_history(self) -> None:
        """修剪历史记录"""
        if len(self._history) > self._max_history_length * 2:
            self._history = self._history[-self._max_history_length :]

    def _build_partial_answer(self) -> str:
        """构建部分答案"""
        if self._history:
            last = self._history[-1]
            return f"在执行 {len(self._steps)} 步后结束。最后的思考: {last.get('thought', '')}"
        return f"执行了 {len(self._steps)} 步但未完成"
