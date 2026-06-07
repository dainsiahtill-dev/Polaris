"""Compatibility re-export for Director tasking code generation.

The implementation lives in
``polaris.cells.director.tasking.internal.code_generation_engine``.
"""

from __future__ import annotations

from polaris.cells.director.tasking.public import (
    CODE_WRITING_FORBIDDEN_WARNING,
    CodeGenerationEngine,
    CodeGenerationPolicyViolationError,
    _raise_policy_violation,
    generate_bootstrap_with_llm,
    generate_fallback_code_content,
    generate_phase_aware_fallback_content,
)

__all__ = [
    "CODE_WRITING_FORBIDDEN_WARNING",
    "CodeGenerationEngine",
    "CodeGenerationPolicyViolationError",
    "_raise_policy_violation",
    "generate_bootstrap_with_llm",
    "generate_fallback_code_content",
    "generate_phase_aware_fallback_content",
]
