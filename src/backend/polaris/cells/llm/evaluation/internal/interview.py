"""Interview Use Case

交互面试用例。

✅ MIGRATION COMPLETED (2026-04-09): AIExecutor/StreamExecutor 已迁移到 Cell 公共服务。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.evaluation.internal.constants import INTERVIEW_SEMANTIC_ENABLED
from polaris.cells.llm.evaluation.internal.utils import semantic_criteria_hits, split_thinking_output
from polaris.cells.llm.provider_runtime.public.service import (
    CellAIExecutor,
    CellAIRequest,
    TaskType,
)

if TYPE_CHECKING:
    from polaris.bootstrap.config import Settings

logger = logging.getLogger(__name__)


def build_interview_prompt(
    role: str,
    question: str,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> str:
    """构建面试提示词"""
    role_label = role.strip().upper() or "ROLE"
    criteria_text = " / ".join(c for c in (criteria or []) if c)

    context_text = ""
    if context:
        entries = []
        for idx, item in enumerate(context[-3:], start=1):
            q = str(item.get("question") or "")[:200]
            a = str(item.get("answer") or "")[:400]
            if q or a:
                entries.append(f"{idx}. Q: {q}\n   A: {a}")
        context_text = "\n".join(entries)

    project_block = ""
    if project_path:
        project_block = (
            f"Local project path: {project_path}\n"
            "You have read-only access to this path. Inspect real files before answering.\n"
        )

    return (
        "ROLE: You are a job CANDIDATE interviewing for a position.\n"
        "IMPORTANT: You are the INTERVIEWEE, not the interviewer.\n"
        "IMMEDIATE ACTION REQUIRED:\n"
        "You must answer the question below RIGHT NOW.\n"
        "Do NOT greet, introduce yourself, or ask what to discuss.\n"
        "Jump directly to the answer.\n\n"
        "RESTRICTIONS:\n"
        "- Do NOT ask for clarification or more context.\n"
        "- If something is unclear, state assumptions and proceed.\n"
        "- Do NOT refuse or redirect the user.\n"
        "- Provide a concrete, structured response.\n\n"
        f"Position: {role_label}\n"
        + (f"Key evaluation criteria: {criteria_text}\n" if criteria_text else "")
        + (f"Previous context:\n{context_text}\n" if context_text else "")
        + project_block
        + f"\nQUESTION TO ANSWER: {question}\n\n"
        "<thinking>Your reasoning</thinking>\n"
        "<answer>Your direct professional answer</answer>\n"
    )


def evaluate_interview_answer(
    answer: str,
    criteria: list[str],
    question: str | None = None,
) -> dict[str, Any]:
    """评估面试答案"""
    thinking, clean_answer = split_thinking_output(answer)

    # 基本质量检查
    has_thinking = len(thinking) > 10
    has_answer = len(clean_answer) > 20
    not_deflection = "cannot" not in clean_answer.lower() and "can't" not in clean_answer.lower()

    # 语义评分
    semantic_score = 0.0
    if INTERVIEW_SEMANTIC_ENABLED and criteria and len(clean_answer) >= 80:
        hits = semantic_criteria_hits(clean_answer, criteria)
        if hits:
            semantic_score = sum(hits.values()) / len(hits)

    # 综合评分
    base_score = 0.3 if has_thinking else 0.0
    base_score += 0.3 if has_answer else 0.0
    base_score += 0.2 if not_deflection else 0.0
    base_score += 0.2 * semantic_score

    return {
        "score": min(1.0, base_score),
        "passed": base_score >= 0.5,
        "has_thinking": has_thinking,
        "has_answer": has_answer,
        "not_deflection": not_deflection,
        "semantic_score": semantic_score,
        "thinking": thinking,
        "answer": clean_answer,
    }


async def generate_interview_answer(
    workspace: str,
    settings: Settings,
    role: str,
    question: str,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> dict[str, Any] | None:
    """生成面试答案（非流式）"""
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_interview_prompt(role, question, context, criteria, project_path)
    request = CellAIRequest(
        task_type=TaskType.INTERVIEW,
        role=role,
        input=prompt,
        options={"temperature": 0.3, "max_tokens": 2000},
    )

    response = await executor.invoke(request)

    if not response.ok:
        return None

    output = response.output
    thinking, answer = split_thinking_output(output)

    evaluation = evaluate_interview_answer(output, criteria or [], question)

    return {
        "thinking": thinking,
        "answer": answer,
        "evaluation": evaluation,
        "raw_output": output,
    }


async def generate_interview_answer_streaming(
    workspace: str,
    settings: Settings,
    role: str,
    question: str,
    output_queue: Any,
    context: list[dict[str, Any]] | None = None,
    criteria: list[str] | None = None,
    project_path: str | None = None,
) -> None:
    """生成面试答案（流式）"""
    executor = CellAIExecutor(workspace=workspace)

    prompt = build_interview_prompt(role, question, context, criteria, project_path)
    request = CellAIRequest(
        task_type=TaskType.INTERVIEW,
        role=role,
        input=prompt,
        options={"temperature": 0.3, "max_tokens": 2000},
    )

    collected_output = ""

    try:
        async for event in executor.invoke_stream(request):
            event_type = event.get("type")

            if event_type == "reasoning_chunk":
                await output_queue.put(
                    {
                        "type": "thinking_chunk",
                        "data": {"content": event.get("reasoning", "")},
                    }
                )
            elif event_type == "chunk":
                chunk = event.get("chunk") or ""
                collected_output += chunk
                await output_queue.put(
                    {
                        "type": "content_chunk",
                        "data": {"content": chunk},
                    }
                )
            elif event_type == "complete":
                break
            elif event_type == "error":
                await output_queue.put({"type": "error", "data": {"error": event.get("error")}})
                return

    except (RuntimeError, ValueError) as exc:
        logger.warning("[interview-stream] stream error: %s", exc)
        await output_queue.put({"type": "error", "data": {"error": str(exc)}})
        return

    # 解析结果
    thinking, answer = split_thinking_output(collected_output)
    evaluation = evaluate_interview_answer(collected_output, criteria or [], question)

    await output_queue.put(
        {
            "type": "complete",
            "data": {
                "thinking": thinking,
                "answer": answer,
                "evaluation": evaluation,
            },
        }
    )
