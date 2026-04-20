"""Plan-Solve Engine - 计划执行引擎

实现 Plan-and-Solve (计划 + 执行) 推理策略：
- 第一阶段：制定详细计划
- 第二阶段：按计划执行
- 适合确定性任务
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from polaris.kernelone.planning import (
    Plan,
    PlanStep,
    StructuralPlanValidator,
    ValidationResult,
)

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
# Plan-Solve 提示词模板
# ═══════════════════════════════════════════════════════════════════════════

PLAN_PHASE_PROMPT = """你是一个使用 Plan-and-Solve 策略的推理引擎。

Plan-and-Solve 策略分两个阶段：
1. 计划阶段：分析任务，制定详细执行计划
2. 执行阶段：按照计划逐步执行

## 当前任务
{task}

## 输出格式
请先分析任务，然后制定计划。输出 JSON 格式：
```json
{{
  "analysis": "任务分析",
  "plan": ["步骤1", "步骤2", "步骤3"],
  "confidence": 0.9
}}
```
"""

EXEC_PHASE_PROMPT = """你正在执行 Plan-and-Solve 策略的执行阶段。

## 原始任务
{task}

## 计划
{plan}

## 已完成步骤
{completed}

## 当前步骤
{current_step}

请执行当前步骤，输出 JSON 格式：
```json
{{
  "thought": "执行当前步骤的思考",
  "action": "工具名称",
  "action_input": {{"参数": "值"}},
  "completed": false,
  "result": "步骤执行结果"
}}
```

当所有步骤完成时：
```json
{{
  "thought": "所有步骤已完成",
  "action": "finish",
  "action_input": {{"answer": "最终答案"}},
  "completed": true
}}
```
"""


class PlanSolveEngine(BaseEngine):
    """Plan-and-Solve 推理引擎

    实现计划+执行策略，适合确定性任务。

    特点：
    - 两阶段执行（计划 + 执行）
    - 先制定详细计划，再按计划执行
    - 适合代码生成、文件创建等任务
    - 计划执行前进行形式化验证
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
        validator: StructuralPlanValidator | None = None,
    ) -> None:
        """初始化 Plan-Solve 引擎

        Args:
            workspace: 工作区路径
            budget: 预算配置
            validator: 计划验证器（默认使用StructuralPlanValidator）
        """
        super().__init__(workspace, budget)
        self._phase: str = "planning"  # planning | executing
        self._plan: list[str] = []
        self._current_plan_index: int = 0
        self._plan_confidence: float = 0.0
        self._validator: StructuralPlanValidator = validator or StructuralPlanValidator()
        self._validation_result: ValidationResult | None = None

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.PLAN_SOLVE

    async def execute(
        self,
        context: EngineContext,
        initial_message: str = "",
    ) -> EngineResult:
        """执行 Plan-Solve 推理

        Args:
            context: 引擎执行上下文
            initial_message: 初始任务描述

        Returns:
            EngineResult: 执行结果
        """
        # 修复：重置引擎状态，避免状态残留
        self.reset()

        self._status = EngineStatus.RUNNING
        self._start_time = time.time()

        # 初始化 Plan-Solve 特定状态
        self._phase = "planning"
        self._plan = []
        self._current_plan_index = 0
        self._plan_confidence = 0.0

        task = initial_message or context.task

        try:
            # 阶段 1: 制定计划
            plan_result = await self._create_plan(context, task)

            if not plan_result["plan"]:
                return self._create_result(
                    success=False,
                    final_answer="无法制定计划",
                    termination_reason="planning_failed",
                )

            self._plan = plan_result["plan"]
            self._plan_confidence = plan_result.get("confidence", 0.5)

            # 验证计划结构
            validation_result = self._validate_plan()
            self._validation_result = validation_result

            if not validation_result.is_valid:
                error_message = validation_result.format_errors()
                logger.warning(f"Plan validation failed: {error_message}")
                return self._create_result(
                    success=False,
                    final_answer=error_message,
                    termination_reason="validation_failed",
                    metadata={
                        "validation_errors": [
                            {"rule_id": v.rule_id, "message": v.message, "location": v.location}
                            for v in validation_result.violations
                            if v.severity.name == "ERROR"
                        ],
                        "validation_suggestions": list(validation_result.suggestions),
                    },
                )

            # 阶段 2: 执行计划
            self._phase = "executing"

            while self.can_continue():
                step_result = await self.step(context)

                self._steps.append(step_result)
                self._current_step += 1

                if step_result.status == EngineStatus.COMPLETED:
                    return self._create_result(
                        success=True,
                        final_answer=step_result.observation,
                        termination_reason="task_completed",
                    )

                if step_result.error:
                    logger.warning(f"Step error: {step_result.error}")

            return self._create_result(
                success=False,
                final_answer=self._build_partial_answer(),
                termination_reason="budget_exhausted",
            )

        except (RuntimeError, ValueError) as e:
            logger.exception("Plan-Solve engine error")
            return self._create_result(
                success=False,
                final_answer=self._build_partial_answer(),
                error=str(e),
                termination_reason="error",
            )

    async def step(self, context: EngineContext) -> StepResult:
        """执行单步 Plan-Solve

        Args:
            context: 引擎执行上下文

        Returns:
            StepResult: 步骤执行结果
        """
        step_index = self._current_step

        if self._phase == "planning":
            # 计划阶段
            return await self._planning_step(context, step_index)
        else:
            # 执行阶段
            return await self._executing_step(context, step_index)

    async def _planning_step(
        self,
        context: EngineContext,
        step_index: int,
    ) -> StepResult:
        """计划阶段的步骤"""
        # 调用 LLM 生成计划
        prompt = PLAN_PHASE_PROMPT.format(task=context.task)
        response = await self._call_llm(context, prompt)

        # 解析计划
        parsed = self._parse_plan_response(response)

        if parsed.get("plan"):
            self._plan = parsed["plan"]
            self._phase = "executing"

            return StepResult(
                step_index=step_index,
                status=EngineStatus.RUNNING,
                thought=parsed.get("analysis", "计划已制定"),
                action="plan_created",
                observation=f"计划包含 {len(self._plan)} 个步骤",
                progress_detected=True,
            )
        else:
            return StepResult(
                step_index=step_index,
                status=EngineStatus.FAILED,
                error="无法解析计划",
            )

    async def _executing_step(
        self,
        context: EngineContext,
        step_index: int,
    ) -> StepResult:
        """执行阶段的步骤"""
        if self._current_plan_index >= len(self._plan):
            # 计划已完成
            return StepResult(
                step_index=step_index,
                status=EngineStatus.COMPLETED,
                observation="所有计划步骤已完成",
                progress_detected=True,
            )

        current_step = self._plan[self._current_plan_index]
        completed_steps = self._plan[: self._current_plan_index]

        # 构建执行提示词
        prompt = EXEC_PHASE_PROMPT.format(
            task=context.task,
            plan="\n".join(f"{i + 1}. {s}" for i, s in enumerate(self._plan)),
            completed="\n".join(f"- {s}" for s in completed_steps) if completed_steps else "无",
            current_step=current_step,
        )

        # 调用 LLM
        response = await self._call_llm(context, prompt)
        parsed = self._parse_exec_response(response)

        action = parsed.get("action", "")
        # 修复：安全获取 action_input，确保类型正确
        action_input_raw = parsed.get("action_input")
        if isinstance(action_input_raw, dict):
            action_input = action_input_raw
        elif action_input_raw is not None:
            action_input = {"value": str(action_input_raw)}
        else:
            action_input = {}
        thought = parsed.get("thought", "")

        # 执行行动
        observation = ""
        tool_result = None
        error_occurred = False

        if action == "finish":
            # 修复：安全获取 answer
            answer = "任务完成"
            if isinstance(action_input, dict):
                answer = action_input.get("answer", "任务完成")
            return StepResult(
                step_index=step_index,
                status=EngineStatus.COMPLETED,
                thought=thought,
                action=action,
                observation=answer,
                progress_detected=True,
            )

        elif action and action not in ("think", "plan_created"):
            try:
                tool_result = await self._execute_tool(context, action, action_input)
                observation = parsed.get("result", str(tool_result)[:500])

                # 检查是否有错误
                if isinstance(tool_result, dict) and "error" in tool_result:
                    error_occurred = True

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

        # 检查是否完成当前步骤
        completed = parsed.get("completed", False)
        if completed:
            self._current_plan_index += 1

        # 判断是否有进展
        progress_detected = bool(observation) and not error_occurred

        return StepResult(
            step_index=step_index,
            status=EngineStatus.RUNNING,
            thought=thought,
            action=action,
            action_input=action_input,
            observation=observation,
            tool_result=tool_result,
            error=str(tool_result.get("error") or "") if error_occurred and isinstance(tool_result, dict) else None,
            progress_detected=progress_detected,
        )

    def can_continue(self) -> bool:
        """检查是否继续执行"""
        if self._status == EngineStatus.COMPLETED:
            return False
        if self._status == EngineStatus.FAILED:
            return False
        if self._current_step >= self.budget.max_steps:
            return False
        if not self._check_budget():
            return False
        return not (self._phase == "executing" and self._current_plan_index >= len(self._plan))

    async def _create_plan(
        self,
        context: EngineContext,
        task: str,
    ) -> dict[str, Any]:
        """创建执行计划"""
        prompt = PLAN_PHASE_PROMPT.format(task=task)
        response = await self._call_llm(context, prompt)
        return self._parse_plan_response(response)

    def _validate_plan(self) -> ValidationResult:
        """Validate the current plan using the structural validator.

        Converts the raw string plan into Plan objects for validation.

        Returns:
            ValidationResult with is_valid and any violations found
        """
        # Convert raw string plan to Plan model
        plan_steps: list[PlanStep] = []
        for idx, step_description in enumerate(self._plan):
            step_id = f"step_{idx + 1}"
            plan_steps.append(
                PlanStep(
                    id=step_id,
                    description=step_description,
                    depends_on=(),
                    estimated_duration=None,
                    metadata={},
                )
            )

        plan = Plan(
            steps=tuple(plan_steps),
            max_duration=None,
            metadata={},
        )

        return self._validator.validate(plan)

    def _parse_plan_response(self, response: str) -> dict[str, Any]:
        """解析计划响应"""
        # 方案1: 尝试解析整个响应为 JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.debug("Full response JSON parse failed, trying fallback")

        # 方案2: 使用括号平衡算法提取 JSON
        try:
            parsed = self._extract_balanced_json(response)
            if parsed:
                return parsed
        except (RuntimeError, ValueError) as exc:
            logger.debug("balance-extract fallback failed: %s", exc)

        # 方案3: 查找 JSON 块
        try:
            json_match = self._find_json_object(response)
            if json_match:
                return json.loads(json_match)
        except json.JSONDecodeError:
            logger.debug("JSON block extraction failed")

        # 方案4: 降级解析：尝试提取 plan 列表
        plan_match = re.findall(r'["\']?(\d+)[.)]\s*([^\n]+)', response)
        if plan_match:
            plan = [step.strip() for _, step in plan_match]
            return {"plan": plan, "analysis": "从文本中提取的计划"}

        return {"plan": [], "analysis": "无法解析"}

    def _parse_exec_response(self, response: str) -> dict[str, Any]:
        """解析执行响应"""
        # 方案1: 尝试解析整个响应为 JSON
        try:
            return json.loads(response)
        except json.JSONDecodeError:
            logger.debug("Full response JSON parse failed, trying fallback")

        # 方案2: 使用括号平衡算法提取 JSON
        try:
            parsed = self._extract_balanced_json(response)
            if parsed:
                return parsed
        except (RuntimeError, ValueError) as exc:
            logger.debug("balance-extract fallback failed: %s", exc)

        # 方案3: 查找 JSON 块
        try:
            json_match = self._find_json_object(response)
            if json_match:
                return json.loads(json_match)
        except json.JSONDecodeError:
            logger.debug("JSON block extraction failed")

        return {"thought": response[:100], "action": "finish", "completed": True}

    def _find_json_object(self, text: str) -> str | None:
        """查找 JSON 对象（支持嵌套）"""
        start = text.find("{")
        if start == -1:
            return None
        end = text.rfind("}")
        if end == -1 or end <= start:
            return None
        return text[start : end + 1]

    def _extract_balanced_json(self, text: str) -> dict[str, Any] | None:
        """使用括号平衡算法提取 JSON"""
        start = text.find("[")
        if start != -1:
            end = self._find_matching_bracket(text, start, "[", "]")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass

        start = text.find("{")
        if start != -1:
            end = self._find_matching_bracket(text, start, "{", "}")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    pass

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
            result = await context.tool_gateway.execute(tool_name, tool_args)
            return result
        else:
            return {"success": True, "message": f"Simulated: {tool_name}"}

    def _build_partial_answer(self) -> str:
        """构建部分答案"""
        if self._plan:
            return f"计划执行了 {self._current_plan_index}/{len(self._plan)} 个步骤"
        return f"执行了 {len(self._steps)} 步但未完成"
