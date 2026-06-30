"""Canonical runtime.v2 channel contracts.

This module is the single source of truth for channel-to-artifact-path mappings.
Historical channel names remain listed here only as compatibility aliases; new
runtime consumers should prefer the canonical ``system`` / ``process`` / ``llm``
journal channels.
"""

from __future__ import annotations

from typing import Final

_RUNTIME_DIR: Final[str] = "runtime"
_RESULTS_DIR: Final[str] = "runtime/results"
_LOGS_DIR: Final[str] = "runtime/logs"
_EVENTS_DIR: Final[str] = "runtime/events"

RUNTIME_V2_JOURNAL_PATH: Final[str] = f"{_RUNTIME_DIR}/runs/{{run_id}}/logs/journal.norm.jsonl"

HISTORICAL_CHANNEL_FILES: Final[dict[str, str]] = {
    "pm_report": f"{_RESULTS_DIR}/pm.report.md",
    "pm_log": f"{_EVENTS_DIR}/pm.events.jsonl",
    "pm_subprocess": f"{_LOGS_DIR}/pm.process.log",
    "pm_llm": f"{_EVENTS_DIR}/pm.llm.events.jsonl",
    "planner": f"{_RESULTS_DIR}/planner.output.md",
    "ollama": f"{_RESULTS_DIR}/director_llm.output.md",
    "qa": f"{_RESULTS_DIR}/qa.review.md",
    "runlog": f"{_LOGS_DIR}/director.runlog.md",
    "dialogue": f"{_EVENTS_DIR}/dialogue.transcript.jsonl",
    "director_console": f"{_LOGS_DIR}/director.process.log",
    "director_llm": f"{_EVENTS_DIR}/director.llm.events.jsonl",
    "engine_status": "runtime/status/engine.status.json",
    "runtime_events": f"{_EVENTS_DIR}/runtime.events.jsonl",
}

CANONICAL_RUNTIME_V2_CHANNEL_FILES: Final[dict[str, str]] = {
    "system": RUNTIME_V2_JOURNAL_PATH,
    "process": RUNTIME_V2_JOURNAL_PATH,
    "llm": RUNTIME_V2_JOURNAL_PATH,
}

CHANNEL_FILES: Final[dict[str, str]] = {
    **HISTORICAL_CHANNEL_FILES,
    **CANONICAL_RUNTIME_V2_CHANNEL_FILES,
}

NEW_CHANNEL_METADATA: Final[dict[str, dict[str, str | list[str]]]] = {
    "system": {
        "description": "System events (runtime, engine status, PM reports)",
        "severity_levels": ["debug", "info", "warn", "error", "critical"],
    },
    "process": {
        "description": "Process output (subprocess stdout/stderr)",
        "severity_levels": ["debug", "info", "warn", "error"],
    },
    "llm": {
        "description": "LLM interaction events",
        "severity_levels": ["debug", "info", "warn", "error"],
    },
}

__all__ = [
    "CANONICAL_RUNTIME_V2_CHANNEL_FILES",
    "CHANNEL_FILES",
    "HISTORICAL_CHANNEL_FILES",
    "NEW_CHANNEL_METADATA",
    "RUNTIME_V2_JOURNAL_PATH",
]
