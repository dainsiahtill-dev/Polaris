"""ToT Engine - 思维树引擎

实现 ToT (Tree of Thoughts) 推理策略：
- 多分支探索
- 分支评估与剪枝
- 适合复杂推理和方案选择
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
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
# ToT 提示词模板
# ═══════════════════════════════════════════════════════════════════════════

TOT_GENERATE_PROMPT = """你正在使用 Tree of Thoughts (ToT) 策略进行推理。

当前任务：{task}

当前思维状态：
{thoughts}

请生成新的思考分支。输出 JSON 格式：
```json
{{
  "thought": "新的思考",
  "reasoning": "推理过程",
  "confidence": 0.8
}}
```

生成 2-3 个不同的思考分支。
"""

TOT_EVALUATE_PROMPT = """你正在评估 Tree of Thoughts (ToT) 中的分支。

任务：{task}

待评估的思考：
{thought}

请评估这个思考的质量。输出 JSON 格式：
```json
{{
  "score": 0.8,
  "reasoning": "评估理由",
  "feasibility": "high/medium/low"
}}
```
"""

TOT_FINISH_PROMPT = """基于以下思考分支，选择最佳方案并给出最终答案。

任务：{task}

候选思考：
{candidates}

请选择最佳方案并给出最终答案。输出 JSON 格式：
```json
{{
  "selected": "最佳方案",
  "answer": "最终答案",
  "reasoning": "选择理由"
}}
```
"""


class BranchStatus(Enum):
    """分支状态"""

    PENDING = "pending"
    EXPANDED = "expanded"
    EVALUATED = "evaluated"
    PRUNED = "pruned"
    COMPLETED = "completed"


@dataclass
class ThoughtBranch:
    """思维分支"""

    id: str
    thought: str
    reasoning: str = ""
    confidence: float = 0.5
    score: float = 0.0
    status: BranchStatus = BranchStatus.PENDING
    parent_id: str | None = None
    children: list[str] = field(default_factory=list)
    depth: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thought": self.thought,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "score": self.score,
            "status": self.status.value,
            "parent_id": self.parent_id,
            "depth": self.depth,
        }


class ToTEngine(BaseEngine):
    """Tree of Thoughts 推理引擎

    实现思维树策略，适合复杂推理。

    特点：
    - 多分支并行探索
    - 分支评估与剪枝
    - 适合架构设计、方案选择等任务
    """

    def __init__(
        self,
        workspace: str = "",
        budget: EngineBudget | None = None,
        max_branches: int = 3,
        max_depth: int = 4,
        pruning_threshold: float = 0.3,
    ) -> None:
        """初始化 ToT 引擎

        Args:
            workspace: 工作区路径
            budget: 预算配置
            max_branches: 最大分支数（建议不超过10）
            max_depth: 最大深度（建议不超过20）
            pruning_threshold: 剪枝阈值
        """
        super().__init__(workspace, budget)
        # 添加上限约束防止资源耗尽
        self.max_branches = min(max(1, max_branches), 10)
        self.max_depth = min(max(1, max_depth), 20)
        self.pruning_threshold = max(0.0, min(1.0, pruning_threshold))

        self._branches: dict[str, ThoughtBranch] = {}
        self._root_id: str | None = None
        self._best_branch_id: str | None = None

    @property
    def strategy(self) -> EngineStrategy:
        return EngineStrategy.TOT

    async def execute(
        self,
        context: EngineContext,
        initial_message: str = "",
    ) -> EngineResult:
        """执行 ToT 推理

        Args:
            context: 引擎执行上下文
            initial_message: 初始任务描述

        Returns:
            EngineResult: 执行结果
        """
        self._status = EngineStatus.RUNNING
        self._start_time = time.time()
        self._branches = {}

        task = initial_message or context.task

        try:
            # 初始化根节点
            await self._initialize_root(context, task)

            # 主循环
            while self.can_continue():
                # 扩展分支
                expanded = await self._expand_branches(context)

                if not expanded:
                    # 无法继续扩展
                    break

                # 评估分支
                await self._evaluate_branches(context)

                # 剪枝
                self._prune_branches()

                self._current_step += 1

            # 选择最佳分支并完成
            return await self._finish(context, task)

        except (RuntimeError, ValueError) as e:
            logger.exception("ToT engine error")
            return self._create_result(
                success=False,
                final_answer=self._build_partial_answer(),
                error=str(e),
                termination_reason="error",
            )

    async def step(self, context: EngineContext) -> StepResult:
        """执行单步 ToT

        Args:
            context: 引擎执行上下文

        Returns:
            StepResult: 步骤执行结果
        """
        # ToT 的 step 是扩展一层分支
        step_index = self._current_step

        # 扩展分支
        await self._expand_branches(context)
        await self._evaluate_branches(context)
        self._prune_branches()

        active_count = len(
            [b for b in self._branches.values() if b.status in (BranchStatus.EXPANDED, BranchStatus.EVALUATED)]
        )

        return StepResult(
            step_index=step_index,
            status=EngineStatus.RUNNING,
            thought=f"扩展了分支，当前有 {active_count} 个活跃分支",
            observation=f"当前深度: {self._get_max_depth()}, 分支数: {len(self._branches)}",
            progress_detected=True,
        )

    def can_continue(self) -> bool:
        """检查是否继续执行"""
        if self._status == EngineStatus.COMPLETED:
            return False
        if self._status == EngineStatus.FAILED:
            return False
        if self._current_step >= self.max_depth:
            return False
        if not self._check_budget():
            return False

        # 检查是否还有可扩展的分支
        expandable = [
            b for b in self._branches.values() if b.status == BranchStatus.EVALUATED and b.depth < self.max_depth
        ]

        # 如果没有可扩展的分支，检查是否还有未评估的分支可以尝试
        if not expandable:
            # 尝试找未评估但可以评估的分支
            unevaluated = [
                b for b in self._branches.values() if b.status in (BranchStatus.PENDING, BranchStatus.EXPANDED)
            ]
            return bool(unevaluated)

        # 确保至少有最少数量的分支
        return not len(expandable) < 1

    async def _initialize_root(
        self,
        context: EngineContext,
        task: str,
    ) -> None:
        """初始化根节点"""
        root = ThoughtBranch(
            id="root",
            thought=task,
            reasoning="初始任务",
            confidence=1.0,
            score=1.0,  # 修复：设置初始分数
            status=BranchStatus.EVALUATED,  # 修复：根节点创建后立即设为已评估状态
            depth=0,
        )
        self._branches["root"] = root
        self._root_id = "root"

    async def _expand_branches(
        self,
        context: EngineContext,
    ) -> bool:
        """扩展分支"""
        # 找到可扩展的分支
        expandable = [
            b for b in self._branches.values() if b.status == BranchStatus.EVALUATED and b.depth < self.max_depth
        ]

        if not expandable:
            # 根节点未评估，先评估根节点
            expandable = [self._branches["root"]] if "root" in self._branches else []

        if not expandable:
            return False

        expanded = False
        for branch in expandable[: self.max_branches]:
            # 生成新分支
            new_thoughts = await self._generate_thoughts(context, branch)

            for thought_data in new_thoughts:
                # 修复：使用 UUID 生成唯一分支ID，避免重复
                branch_id = f"branch_{uuid.uuid4().hex[:8]}"
                new_branch = ThoughtBranch(
                    id=branch_id,
                    thought=thought_data.get("thought", ""),
                    reasoning=thought_data.get("reasoning", ""),
                    confidence=thought_data.get("confidence", 0.5),
                    parent_id=branch.id,
                    depth=branch.depth + 1,
                    status=BranchStatus.EXPANDED,
                )
                self._branches[branch_id] = new_branch
                branch.children.append(branch_id)
                expanded = True

        return expanded

    async def _generate_thoughts(
        self,
        context: EngineContext,
        parent_branch: ThoughtBranch,
    ) -> list[dict[str, Any]]:
        """生成新思考"""
        # 构建当前思维状态
        thoughts_state = "\n".join(
            f"- {b.thought[:100]} (score: {b.score:.2f})"
            for b in self._branches.values()
            if b.status in (BranchStatus.EVALUATED, BranchStatus.COMPLETED)
        )

        prompt = TOT_GENERATE_PROMPT.format(
            task=context.task,
            thoughts=thoughts_state or "无",
        )

        response = await self._call_llm(context, prompt)
        return self._parse_thoughts_response(response)

    async def _evaluate_branches(self, context: EngineContext) -> None:
        """评估分支"""
        # 评估所有新扩展的分支
        for branch in self._branches.values():
            if branch.status != BranchStatus.EXPANDED:
                continue

            prompt = TOT_EVALUATE_PROMPT.format(
                task=context.task,
                thought=branch.thought,
            )

            response = await self._call_llm(context, prompt)
            evaluation = self._parse_evaluation_response(response)

            branch.score = evaluation.get("score", branch.confidence)
            branch.status = BranchStatus.EVALUATED

    def _prune_branches(self) -> None:
        """剪枝低分分支"""
        if not self._branches:
            return

        # 计算平均分
        scores = [b.score for b in self._branches.values() if b.score > 0]
        if not scores:
            return

        avg_score = sum(scores) / len(scores)
        # 使用 min 防止阈值过高导致所有分支被剪枝
        threshold = min(self.pruning_threshold, avg_score * 0.8)

        # 剪枝
        pruned = []
        for branch in self._branches.values():
            # 只剪枝 EXPANDED 状态且得分低于阈值的分支
            if branch.status == BranchStatus.EXPANDED and branch.score < threshold:
                branch.status = BranchStatus.PRUNED
                pruned.append(branch.id)

        if pruned:
            logger.debug(f"Pruned branches: {pruned}")

    async def _finish(
        self,
        context: EngineContext,
        task: str,
    ) -> EngineResult:
        """完成推理，选择最佳分支"""
        # 选择得分最高的分支
        evaluated = [b for b in self._branches.values() if b.status == BranchStatus.EVALUATED]

        if not evaluated:
            return self._create_result(
                success=False,
                final_answer="无法完成推理",
                termination_reason="no_evaluated_branches",
            )

        best_branch = max(evaluated, key=lambda b: b.score)
        self._best_branch_id = best_branch.id

        # 获取完整路径
        path = self._get_branch_path(best_branch.id)

        # 构建完成提示
        candidates = "\n".join(
            f"- {b.thought[:100]} (score: {b.score:.2f})"
            for b in sorted(evaluated, key=lambda x: x.score, reverse=True)[:5]
        )

        prompt = TOT_FINISH_PROMPT.format(
            task=task,
            candidates=candidates,
        )

        response = await self._call_llm(context, prompt)
        finish_result = self._parse_finish_response(response)

        # 记录步骤
        self._steps.append(
            StepResult(
                step_index=self._current_step,
                status=EngineStatus.COMPLETED,
                thought=finish_result.get("reasoning", ""),
                observation=finish_result.get("answer", best_branch.thought),
                progress_detected=True,
            )
        )

        return self._create_result(
            success=True,
            final_answer=finish_result.get("answer", best_branch.thought),
            termination_reason="task_completed",
            metadata={
                "best_branch_id": best_branch.id,
                "best_score": best_branch.score,
                "total_branches": len(self._branches),
                "max_depth": self._get_max_depth(),
                "branch_path": path,
            },
        )

    def _get_branch_path(self, branch_id: str) -> list[str]:
        """获取分支路径"""
        path: list[str] = []
        current_id: str | None = branch_id

        while current_id and current_id in self._branches:
            branch = self._branches[current_id]
            path.insert(0, branch.thought[:50])
            current_id = branch.parent_id

        return path

    def _get_max_depth(self) -> int:
        """获取最大深度"""
        if not self._branches:
            return 0
        return max(b.depth for b in self._branches.values())

    def _parse_thoughts_response(self, response: str) -> list[dict[str, Any]]:
        """解析思考生成响应"""
        # 方案1: 尝试解析整个响应为 JSON
        try:
            result = json.loads(response)
            if isinstance(result, list):
                return result
            elif isinstance(result, dict):
                return [result]
        except json.JSONDecodeError as exc:
            logger.debug("tot: thoughts JSON parse failed (trying fallbacks): %s", exc)

        # 方案2: 查找 JSON 数组（使用更健壮的方法）
        try:
            # 使用括号平衡算法
            parsed = self._extract_balanced_json(response)
            if parsed and isinstance(parsed, list):
                return parsed
            elif parsed and isinstance(parsed, dict):
                return [parsed]
        except (RuntimeError, ValueError):
            logger.debug("tot: thoughts balanced extraction failed", exc_info=True)

        # 方案3: 使用正则查找 JSON 数组
        try:
            json_match = re.search(r"\[[\s\S]*\]", response)
            if json_match:
                result = json.loads(json_match.group())
                if isinstance(result, list):
                    return result
        except json.JSONDecodeError as exc:
            logger.debug("tot: thoughts regex JSON parse failed: %s", exc)

        # 降级返回单个思考
        return [{"thought": response[:100], "reasoning": "", "confidence": 0.5}]

    def _parse_evaluation_response(self, response: str) -> dict[str, Any]:
        """解析评估响应"""
        # 方案1: 尝试解析整个响应
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            logger.debug("tot: evaluation JSON parse failed (trying fallbacks): %s", exc)

        # 方案2: 使用括号平衡算法
        try:
            parsed = self._extract_balanced_json(response)
            if parsed:
                return parsed
        except (RuntimeError, ValueError):
            logger.debug("tot: evaluation balanced extraction failed", exc_info=True)

        # 方案3: 使用正则
        try:
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            logger.debug("tot: evaluation regex JSON parse failed: %s", exc)

        return {"score": 0.5, "reasoning": ""}

    def _parse_finish_response(self, response: str) -> dict[str, Any]:
        """解析完成响应"""
        # 方案1: 尝试解析整个响应
        try:
            return json.loads(response)
        except json.JSONDecodeError as exc:
            logger.debug("tot: finish JSON parse failed (trying fallbacks): %s", exc)

        # 方案2: 使用括号平衡算法
        try:
            parsed = self._extract_balanced_json(response)
            if parsed:
                return parsed
        except (RuntimeError, ValueError):
            logger.debug("tot: finish balanced extraction failed", exc_info=True)

        # 方案3: 使用正则
        try:
            json_match = re.search(r"\{[\s\S]*\}", response)
            if json_match:
                return json.loads(json_match.group())
        except json.JSONDecodeError as exc:
            logger.debug("tot: finish regex JSON parse failed: %s", exc)

        return {"answer": response[:200], "reasoning": ""}

    def _extract_balanced_json(self, text: str) -> Any | None:
        """使用括号平衡算法提取 JSON"""
        # 优先尝试对象（因为任务描述通常是 JSON 对象而非裸数组）
        start = text.find("{")
        if start != -1:
            end = self._find_matching_bracket(text, start, "{", "}")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    logger.debug("tot: balanced object JSON decode failed: %s", exc)

        # 尝试找数组
        start = text.find("[")
        if start != -1:
            end = self._find_matching_bracket(text, start, "[", "]")
            if end != -1:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError as exc:
                    logger.debug("tot: balanced array JSON decode failed: %s", exc)

        return None

    def _find_matching_bracket(
        self,
        text: str,
        start: int,
        open_char: str,
        close_char: str,
    ) -> int:
        """查找匹配的闭合括号位置

        Args:
            text: 文本内容
            start: 开始括号的位置
            open_char: 开括号字符 ('{' 或 '[')
            close_char: 闭括号字符 ('}' 或 ']')

        Returns:
            匹配的闭括号位置，如果未找到返回 -1
        """
        depth = 0
        i = start
        while i < len(text):
            if text[i] == open_char:
                depth += 1
            elif text[i] == close_char:
                depth -= 1
                if depth == 0:
                    return i
            i += 1
        return -1

    def _build_partial_answer(self) -> str:
        """构建部分答案"""
        if not self._branches:
            return "未生成任何分支"

        evaluated = [b for b in self._branches.values() if b.status == BranchStatus.EVALUATED]
        if evaluated:
            best = max(evaluated, key=lambda b: b.score)
            return f"最佳思考: {best.thought[:100]}..."

        return f"生成了 {len(self._branches)} 个分支"
