"""Dynamic temperature strategy for LLM invocations.

Different roles and task phases need different temperatures:
- Creative tasks (PM planning): higher temperature for diverse ideas
- Code generation (Director): low temperature for consistent, correct output
- Repair/fix: very low temperature for precise corrections
- Quality review (QA): low temperature for precise analysis

This module provides a centralized temperature resolver that the LLM
invocation pipeline can query based on role + phase context.
"""

from __future__ import annotations

from dataclasses import dataclass

# Temperature by role (base defaults)
_ROLE_TEMPERATURES: dict[str, float] = {
    "pm": 0.5,
    "architect": 0.4,
    "chief_engineer": 0.4,
    "director": 0.15,
    "qa": 0.2,
    "scout": 0.3,
}

# Phase-specific overrides (role, phase) → temperature
# Finer granularity: each role × phase combination has an optimized temperature
_PHASE_OVERRIDES: dict[tuple[str, str], float] = {
    # ── PM (项目管理) ──
    # 创意/发散型任务 → 较高温度
    ("pm", "planning"): 0.6,
    ("pm", "task_creation"): 0.6,
    ("pm", "task_split"): 0.55,
    ("pm", "brainstorming"): 0.7,
    ("pm", "requirement_analysis"): 0.5,
    ("pm", "scope_definition"): 0.5,
    ("pm", "backlog_grooming"): 0.5,
    ("pm", "sprint_planning"): 0.45,
    # 分析/决策型任务 → 较低温度
    ("pm", "risk_assessment"): 0.3,
    ("pm", "risk_register"): 0.3,
    ("pm", "decision"): 0.3,
    ("pm", "decision_log"): 0.25,
    ("pm", "status_report"): 0.2,
    ("pm", "project_report"): 0.2,
    ("pm", "wsjf_scoring"): 0.2,
    ("pm", "milestone_tracking"): 0.2,
    ("pm", "acceptance_criteria"): 0.3,
    ("pm", "contract_generation"): 0.3,
    # 沟通/汇总 → 中等温度
    ("pm", "summary"): 0.4,
    ("pm", "chat"): 0.5,
    # ── Architect (架构师) ──
    ("architect", "design"): 0.45,
    ("architect", "architecture_review"): 0.35,
    ("architect", "tech_selection"): 0.4,
    ("architect", "api_design"): 0.3,
    ("architect", "data_model"): 0.3,
    # ── Chief Engineer (技术总监) ──
    # 分析型 → 中低温度
    ("chief_engineer", "blueprint"): 0.4,
    ("chief_engineer", "blueprint_generation"): 0.35,
    ("chief_engineer", "technical_analysis"): 0.35,
    ("chief_engineer", "code_review"): 0.3,
    ("chief_engineer", "risk_register"): 0.3,
    ("chief_engineer", "tech_debt_assessment"): 0.3,
    ("chief_engineer", "quality_gate"): 0.25,
    ("chief_engineer", "interface_design"): 0.3,
    ("chief_engineer", "type_signature"): 0.2,
    # 探索/推理 → 中等温度
    ("chief_engineer", "exploration"): 0.45,
    ("chief_engineer", "reasoning"): 0.4,
    ("chief_engineer", "chat"): 0.45,
    # ── Director (代码执行) ──
    # 代码生成 → 低温度（一致性最重要）
    ("director", "code_generation"): 0.15,
    ("director", "materialize"): 0.15,
    ("director", "scaffold"): 0.2,
    ("director", "file_write"): 0.1,
    ("director", "implementation"): 0.15,
    ("director", "test_generation"): 0.2,
    ("director", "boilerplate"): 0.1,
    # 修复/重试 → 极低温度（精确性最重要）
    ("director", "repair"): 0.05,
    ("director", "fix"): 0.05,
    ("director", "verification_repair"): 0.05,
    ("director", "deterministic_repair"): 0.0,
    ("director", "retry"): 0.1,
    ("director", "retry_round_1"): 0.1,
    ("director", "retry_round_2"): 0.08,
    ("director", "retry_round_3"): 0.05,
    ("director", "final_retry"): 0.02,
    # 工具调用 → 极低温度（参数必须准确）
    ("director", "tool_call"): 0.05,
    ("director", "command_generation"): 0.1,
    ("director", "edit_blocks"): 0.08,
    # 代码阅读/分析 → 中等温度
    ("director", "code_read"): 0.3,
    ("director", "exploration"): 0.35,
    ("director", "chat"): 0.4,
    # ── QA (质量审查) ──
    ("qa", "review"): 0.2,
    ("qa", "verdict"): 0.15,
    ("qa", "audit"): 0.2,
    ("qa", "test_verification"): 0.15,
    ("qa", "claim_check"): 0.15,
    ("qa", "integration_test"): 0.2,
    ("qa", "smoke_test"): 0.2,
    ("qa", "report"): 0.25,
    # ── Scout (代码探索) ──
    ("scout", "code_search"): 0.3,
    ("scout", "doc_search"): 0.3,
    ("scout", "exploration"): 0.35,
    ("scout", "summarization"): 0.3,
}

# Default fallback
_DEFAULT_TEMPERATURE = 0.4

# Absolute bounds
_MIN_TEMPERATURE = 0.0
_MAX_TEMPERATURE = 1.0


@dataclass(frozen=True)
class TemperatureContext:
    """Context for resolving the appropriate LLM temperature."""

    role: str
    phase: str = ""
    is_retry: bool = False
    is_repair: bool = False
    user_override: float | None = None


def resolve_temperature(ctx: TemperatureContext) -> float:
    """Resolve the appropriate temperature for an LLM invocation.

    Resolution order:
    1. User explicit override (highest priority)
    2. Repair/retry special handling (very low temp)
    3. Phase-specific override for the role
    4. Role base default
    5. Global default fallback

    Returns:
        Temperature value clamped to [0.0, 1.0].
    """
    # 1. User explicit override
    if ctx.user_override is not None:
        return _clamp(ctx.user_override)

    # 2. Repair/retry: very low temperature for precision
    if ctx.is_repair:
        return 0.05
    if ctx.is_retry:
        return 0.1

    role = str(ctx.role or "").strip().lower()
    phase = str(ctx.phase or "").strip().lower()

    # 3. Phase-specific override
    if role and phase:
        override = _PHASE_OVERRIDES.get((role, phase))
        if override is not None:
            return _clamp(override)
        # Try partial phase match (e.g., "repair" in "verification_repair_round_2")
        for (r, p), temp in _PHASE_OVERRIDES.items():
            if r == role and p in phase:
                return _clamp(temp)

    # 4. Role base default
    if role in _ROLE_TEMPERATURES:
        return _ROLE_TEMPERATURES[role]

    # 5. Global default
    return _DEFAULT_TEMPERATURE


def _clamp(value: float) -> float:
    """Clamp temperature to valid range [0.0, 1.0]."""
    return max(_MIN_TEMPERATURE, min(_MAX_TEMPERATURE, float(value)))


def get_role_temperature(role: str) -> float:
    """Get the base temperature for a role (convenience function)."""
    return _ROLE_TEMPERATURES.get(str(role or "").strip().lower(), _DEFAULT_TEMPERATURE)


def get_phase_temperature(role: str, phase: str) -> float | None:
    """Get the phase-specific temperature override, or None if no override."""
    role = str(role or "").strip().lower()
    phase = str(phase or "").strip().lower()
    override = _PHASE_OVERRIDES.get((role, phase))
    if override is not None:
        return override
    # Partial match
    for (r, p), temp in _PHASE_OVERRIDES.items():
        if r == role and p in phase:
            return temp
    return None
