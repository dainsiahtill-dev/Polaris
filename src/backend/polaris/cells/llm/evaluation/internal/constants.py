"""Evaluation Framework - Constants"""

from __future__ import annotations

import os

THINKING_INDICATORS = [
    "<thinking>",
    "<reasoning>",
    "let me think",
    "step by step",
    "my reasoning",
    "i need to consider",
    "thought process",
    "analysis:",
    "reasoning:",
]


ROLE_REQUIREMENTS = {
    "pm": {
        "requires_thinking": True,
        "min_confidence": 0.7,
        "error_message": "PM 岗位需要具备深度思考能力的模型",
    },
    "director": {
        "requires_thinking": True,
        "min_confidence": 0.7,
        "error_message": "Director 岗位需要具备推理能力的模型",
    },
}


def _env_flag(name: str, default: bool = True) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    if value.lower() in ("1", "true", "yes", "on"):
        return True
    if value.lower() in ("0", "false", "no", "off"):
        return False
    return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


INTERVIEW_SEMANTIC_ENABLED = _env_flag("KERNELONE_INTERVIEW_SEMANTIC", True)
INTERVIEW_SEMANTIC_THRESHOLD = _env_float("KERNELONE_INTERVIEW_SEMANTIC_THRESHOLD", 0.78)
INTERVIEW_SEMANTIC_MIN_CHARS = _env_int("KERNELONE_INTERVIEW_SEMANTIC_MIN_CHARS", 80)
INTERVIEW_SEMANTIC_MAX_CHARS = _env_int("KERNELONE_INTERVIEW_SEMANTIC_MAX_CHARS", 2000)
INTERVIEW_SEMANTIC_TIMEOUT = _env_float("KERNELONE_INTERVIEW_SEMANTIC_TIMEOUT", 3.0)
INTERVIEW_EMBEDDING_MODEL = os.environ.get(
    "KERNELONE_INTERVIEW_EMBEDDING_MODEL",
    os.environ.get("KERNELONE_EMBEDDING_MODEL", "nomic-embed-text"),
)


# Suite types
SUITES = [
    "connectivity",
    "response",
    "thinking",
    "qualification",
    "interview",
    "agentic_benchmark",
    "tool_calling_matrix",
]

# Required suites per role
REQUIRED_SUITES_BY_ROLE: dict[str, list[str]] = {
    "pm": ["connectivity", "response", "qualification", "thinking", "interview"],
    "director": ["connectivity", "response", "qualification", "thinking", "interview"],
    "architect": ["connectivity", "response", "qualification", "thinking"],
    "default": ["connectivity", "response", "qualification"],
}
