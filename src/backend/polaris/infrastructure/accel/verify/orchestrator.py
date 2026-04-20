"""Facade for verify orchestrator - redirects to refactored submodule.

This file maintains backward compatibility by importing from the verify submodule.
All internal functions prefixed with '_' are preserved for any legacy imports.

Refactored on 2026-03-31 from 2679 lines to this facade.
Original modules now in verify/ subdirectory:
- core.py (331 lines) - VerifyOrchestrator core logic
- formatters.py (190 lines) - Command parsing and formatting
- gate_checker.py (219 lines) - Preflight and gate checking
- report_generator.py (313 lines) - Logging and report generation
- runner_utils.py (143 lines) - Runner utilities
- cli.py (98 lines) - CLI entry point
"""

from __future__ import annotations

# Public API - import from refactored submodule
from .verify.core import (
    VerifyConfig,
    VerifyResult,
    run_verify,
    run_verify_with_callback,
)

# Internal helpers - preserved for backward compatibility

__all__ = [
    # Public API
    "VerifyConfig",
    "VerifyResult",
    "run_verify",
    "run_verify_with_callback",
]
