"""Unified CLI entry point for ``python -m polaris.delivery.cli``.

Subcommands:
  console   Launch the canonical terminal console host
  task     Task management (create / list / show)
  session  Session management (list / show / switch / clear)
  serve    Start the backend HTTP server
  cell     Cell catalog (list / info)
  chat     Legacy role chat (interactive / oneshot / server)
  status   Query role runtime status
  workflow Run or inspect Polaris workflow executions

Global flags:
  --workspace, -w   Workspace directory (default: cwd)
  --session-id       Reuse a specific session ID
  --no-persist      Skip persisting state to disk
  --log-level       CLI logging verbosity (debug/info/warn/error/critical)

Architecture:
  - Argument parsing only (this file)
  - Dispatch via CliRouter (router.py)
  - Backend bindings installed lazily on first command
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from polaris.delivery.cli.logging_policy import (
    CLI_LOG_LEVEL_CHOICES,
    configure_cli_logging,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from polaris.delivery.cli.router import CliRouter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def _bootstrap_runtime() -> None:
    """Install minimal KernelOne + provider bindings (best-effort)."""
    try:
        from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
        from polaris.infrastructure.llm.provider_bootstrap import inject_kernelone_provider_runtime

        ensure_minimal_kernelone_bindings()
        inject_kernelone_provider_runtime()
    except (RuntimeError, ValueError) as exc:  # pragma: no cover — defensive bootstrap
        logger.debug("CLI bootstrap warning (non-fatal): %s", exc)


def _enforce_utf8() -> None:
    """Enforce UTF-8 for this process."""
    import contextlib
    import locale

    os.environ["PYTHONUTF8"] = "1"
    os.environ["PYTHONIOENCODING"] = "utf-8"
    with contextlib.suppress(Exception):  # pragma: no cover — locale may not be available
        locale.setlocale(locale.LC_ALL, "")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

_ALLOWED_ROLES = ("director", "pm", "architect", "chief_engineer", "qa")
_BACKEND_CHOICES = ("auto", "textual", "rich", "plain")
_PROMPT_STYLE_CHOICES = ("plain", "omp")
_JSON_RENDER_CHOICES = ("raw", "pretty", "pretty-color")
_OUTPUT_FORMAT_CHOICES = ("text", "json", "json-pretty", "json-stream")
_AGENTIC_EVAL_ROLE_CHOICES = ("all", "director", "pm", "architect", "chief_engineer", "qa")
_AGENTIC_EVAL_SUITE_CHOICES = ("agentic_benchmark", "tool_calling_matrix")


def _add_workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=".",
        help="Workspace directory (default: cwd)",
    )


def _add_session_id_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--session-id",
        type=str,
        default="",
        help="Reuse a specific session ID when supported",
    )


def _add_log_level_argument(
    parser: argparse.ArgumentParser,
    *,
    default: str | None | object = None,
) -> None:
    parser.add_argument(
        "--log-level",
        choices=CLI_LOG_LEVEL_CHOICES,
        default=default,
        help=("CLI logging level. Supports debug/info/warn/warning/error/critical (or env POLARIS_CLI_LOG_LEVEL)."),
    )


def create_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="polaris",
        description="Polaris unified CLI — one host, multi-role, multi-mode",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_workspace_argument(parser)
    _add_session_id_argument(parser)
    parser.add_argument(
        "--no-persist",
        action="store_true",
        default=False,
        help="Skip persisting CLI state to disk",
    )
    _add_log_level_argument(parser, default=None)

    subparsers = parser.add_subparsers(dest="command", required=True, title="commands")

    # ── console ─────────────────────────────────────────────────────────────
    console_parser = subparsers.add_parser(
        "console",
        help="Launch the canonical terminal console host",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Open the Polaris terminal console host. "
            "Backend defaults to 'auto'; legacy textual/rich values are accepted for compatibility."
        ),
    )
    _add_workspace_argument(console_parser)
    _add_session_id_argument(console_parser)
    _add_log_level_argument(console_parser, default=argparse.SUPPRESS)
    console_parser.add_argument(
        "--backend",
        choices=_BACKEND_CHOICES,
        default="auto",
        help="Console backend (default: auto)",
    )
    console_parser.add_argument(
        "--role",
        choices=list(_ALLOWED_ROLES),
        default="director",
        help="Fallback role to launch (default: director). When --super is enabled, used as fallback route.",
    )
    console_parser.add_argument(
        "--super",
        action="store_true",
        default=False,
        help="Enable SUPER mode: dynamically route each request across PM/Director/Architect/QA by intent.",
    )
    console_parser.add_argument(
        "--session-title",
        type=str,
        default="",
        help="Title for a newly created session",
    )
    console_parser.add_argument(
        "--prompt-style",
        choices=_PROMPT_STYLE_CHOICES,
        default="plain",
        help="Prompt style (default: plain)",
    )
    console_parser.add_argument(
        "--omp-config",
        type=str,
        default="",
        help="Optional Oh My Posh config path (used when --prompt-style omp)",
    )
    console_parser.add_argument(
        "--json-render",
        choices=_JSON_RENDER_CHOICES,
        default="raw",
        help="Tool event JSON rendering mode (default: raw)",
    )
    console_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable unified debug/observability stream for console mode",
    )
    console_parser.add_argument(
        "--batch",
        action="store_true",
        default=False,
        help=(
            "Batch mode: read entire stdin as a single message, stream output, exit on complete. "
            "Auto-detected when stdin is not a tty."
        ),
    )
    console_parser.add_argument(
        "--output-format",
        choices=_OUTPUT_FORMAT_CHOICES,
        default=None,
        help=(
            "Output format for streamed events: 'text' (default, human-readable), "
            "'json' (single-line NDJSON), 'json-pretty' (pretty-printed), "
            "'json-stream' (alias for json). "
            "When stdout is not a TTY, defaults to 'json' automatically. "
            "Backward compatible with --json-render."
        ),
    )
    console_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run in dry-run mode: show tool calls without executing them",
    )
    console_parser.add_argument(
        "--cognitive-mode",
        choices=["off", "basic", "full"],
        default="basic",
        help=(
            "Cognitive Life Form mode: "
            "off (disable cognitive middleware), "
            "basic (enable middleware only, advanced features off), "
            "full (enable all cognitive features: evolution, governance, value alignment, LLM reasoning). "
            "(default: basic)"
        ),
    )

    # ── task ────────────────────────────────────────────────────────────────
    task_parser = subparsers.add_parser(
        "task",
        help="Task management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage Polaris tasks via the task runtime service.",
    )
    _add_workspace_argument(task_parser)
    _add_log_level_argument(task_parser, default=argparse.SUPPRESS)
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True, title="task commands")

    # task create
    task_create = task_subparsers.add_parser("create", help="Create a new task")
    task_create.add_argument("--subject", required=True, help="Task subject (required)")
    task_create.add_argument("--description", default="", help="Task description")
    task_create.add_argument(
        "--priority",
        default="MEDIUM",
        choices=["CRITICAL", "HIGH", "MEDIUM", "LOW"],
        help="Priority level (default: MEDIUM)",
    )
    task_create.add_argument(
        "--blocked-by",
        nargs="*",
        default=[],
        help="IDs of blocking tasks",
    )

    # task list
    task_list = task_subparsers.add_parser("list", help="List tasks")
    task_list.add_argument(
        "--include-terminal",
        default="yes",
        choices=["yes", "no"],
        help="Include completed/cancelled tasks (default: yes)",
    )

    # task show
    task_show = task_subparsers.add_parser("show", help="Show a specific task")
    task_show.add_argument("--task-id", required=True, help="Task ID to display")

    # ── session ─────────────────────────────────────────────────────────────
    session_parser = subparsers.add_parser(
        "session",
        help="Session management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage role console sessions (list / show / switch / clear).",
    )
    _add_workspace_argument(session_parser)
    _add_session_id_argument(session_parser)
    _add_log_level_argument(session_parser, default=argparse.SUPPRESS)
    session_parser.add_argument(
        "--role",
        choices=list(_ALLOWED_ROLES),
        default="director",
        help="Role whose sessions to manage (default: director)",
    )
    session_subparsers = session_parser.add_subparsers(dest="session_command", required=True, title="session commands")

    # session list
    session_list = session_subparsers.add_parser("list", help="List sessions")
    session_list.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of sessions to return (default: 20)",
    )
    session_list.add_argument(
        "--state",
        default="",
        help="Filter by session state (e.g. active)",
    )
    session_list.add_argument(
        "--role",
        default="",
        help="Filter by role",
    )

    # session show
    session_show = session_subparsers.add_parser("show", help="Show a specific session")
    session_show.add_argument("--session-id", required=True, help="Session ID to display")

    # session switch
    session_switch = session_subparsers.add_parser("switch", help="Switch to a specific session")
    session_switch.add_argument("--session-id", required=True, help="Session ID to switch to")

    # session clear
    session_clear = session_subparsers.add_parser("clear", help="Deactivate sessions for a role")
    session_clear.add_argument(
        "--role",
        default="director",
        choices=list(_ALLOWED_ROLES),
        help="Role whose sessions to clear (default: director)",
    )

    # ── serve ──────────────────────────────────────────────────────────────
    serve_parser = subparsers.add_parser(
        "serve",
        help="Start the backend HTTP server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Start the Polaris FastAPI backend server on the given host:port.",
    )
    _add_workspace_argument(serve_parser)
    _add_log_level_argument(serve_parser, default=argparse.SUPPRESS)
    serve_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    serve_parser.add_argument(
        "--port",
        type=int,
        default=49977,
        help="Bind port (default: 49977)",
    )

    # ── cell ───────────────────────────────────────────────────────────────
    cell_parser = subparsers.add_parser(
        "cell",
        help="Cell catalog operations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List or inspect cells registered in the Polaris catalog.",
    )
    _add_workspace_argument(cell_parser)
    _add_log_level_argument(cell_parser, default=argparse.SUPPRESS)
    cell_subparsers = cell_parser.add_subparsers(dest="cell_command", required=True, title="cell commands")

    cell_subparsers.add_parser("list", help="List all registered cells")
    cell_info = cell_subparsers.add_parser("info", help="Show info for a specific cell")
    cell_info.add_argument("--cell-id", required=True, help="Cell ID to inspect")

    # ── chat (legacy alias) ─────────────────────────────────────────────────
    chat_parser = subparsers.add_parser(
        "chat",
        help="Run a role through the canonical Polaris host (legacy)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Legacy entrypoint — equivalent to 'polaris-cli chat' in older releases. "
        "Prefer the 'console' subcommand for new workflows.",
    )
    _add_workspace_argument(chat_parser)
    _add_log_level_argument(chat_parser, default=argparse.SUPPRESS)
    chat_parser.add_argument(
        "--role",
        default="director",
        choices=list(_ALLOWED_ROLES),
        help="Role id (default: director)",
    )
    chat_parser.add_argument(
        "--mode",
        choices=["interactive", "oneshot", "server", "console"],
        default="interactive",
        help="Host mode (default: interactive)",
    )
    chat_parser.add_argument("--goal", default="", help="Goal/message for oneshot mode")
    chat_parser.add_argument("--host", default="127.0.0.1", help="Server bind host")
    chat_parser.add_argument("--port", type=int, default=50000, help="Server bind port")
    chat_parser.add_argument(
        "--backend",
        choices=_BACKEND_CHOICES,
        default="auto",
        help="Console backend (console mode; textual/rich kept for compatibility)",
    )
    chat_parser.add_argument(
        "--prompt-style",
        choices=_PROMPT_STYLE_CHOICES,
        default="plain",
        help="Prompt style (console mode, default: plain)",
    )
    chat_parser.add_argument(
        "--omp-config",
        type=str,
        default="",
        help="Optional Oh My Posh config path (console mode)",
    )
    chat_parser.add_argument(
        "--json-render",
        choices=_JSON_RENDER_CHOICES,
        default="raw",
        help="Tool event JSON rendering mode (console mode, default: raw)",
    )
    chat_parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable unified debug/observability stream for console mode",
    )
    _add_session_id_argument(chat_parser)
    chat_parser.add_argument("--session-title", default="", help="Title for a new console session")

    # ── status ─────────────────────────────────────────────────────────────
    status_parser = subparsers.add_parser("status", help="Query runtime status for available roles")
    _add_workspace_argument(status_parser)
    _add_log_level_argument(status_parser, default=argparse.SUPPRESS)
    status_parser.add_argument("--role", default="", help="Optional role filter")

    # ── workflow ───────────────────────────────────────────────────────────
    workflow_parser = subparsers.add_parser(
        "workflow",
        help="Run or inspect Polaris workflow executions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Manage PM workflow executions (run / status / events / cancel).",
    )
    _add_workspace_argument(workflow_parser)
    _add_log_level_argument(workflow_parser, default=argparse.SUPPRESS)
    workflow_parser.add_argument(
        "workflow_action",
        choices=["run", "status", "events", "cancel"],
        help="Workflow action",
    )
    workflow_parser.add_argument(
        "workflow_target",
        nargs="?",
        default="",
        help="Workflow target for run actions (currently: pm)",
    )
    workflow_parser.add_argument("--workflow-id", default="", help="Workflow id for status/events/cancel")
    workflow_parser.add_argument(
        "--contracts-file",
        default="runtime/contracts/pm_tasks.contract.json",
        help="PM contract JSON file relative to workspace",
    )
    workflow_parser.add_argument("--run-id", default="", help="Explicit workflow run id")
    workflow_parser.add_argument("--message", default="", help="Optional operator note")
    workflow_parser.add_argument(
        "--wait",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Wait for terminal workflow completion after submission",
    )
    workflow_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=300.0,
        help="Wait timeout in seconds when --wait is enabled",
    )
    workflow_parser.add_argument(
        "--event-limit",
        type=int,
        default=100,
        help="Maximum number of workflow events to load",
    )
    workflow_parser.add_argument(
        "--reason",
        default="operator_cancelled",
        help="Cancellation reason for workflow cancel",
    )
    workflow_parser.add_argument(
        "--execution-mode",
        choices=["parallel", "serial"],
        default="parallel",
        help="Director execution mode",
    )
    workflow_parser.add_argument(
        "--max-parallel-tasks",
        type=int,
        default=3,
        help="Maximum parallel Director tasks",
    )
    workflow_parser.add_argument(
        "--ready-timeout-seconds",
        type=int,
        default=30,
        help="Ready-task resolution timeout for Director workflow",
    )
    workflow_parser.add_argument(
        "--task-timeout-seconds",
        type=int,
        default=3600,
        help="Per-task timeout for Director workflow",
    )

    # ── agentic-eval ───────────────────────────────────────────────────────
    agentic_eval_parser = subparsers.add_parser(
        "agentic-eval",
        help="Run deterministic agentic benchmark with score + audit + repair plan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run llm.evaluation agentic benchmark in one command and emit "
            "score, failure root causes, tool audit, and deterministic repair plan."
        ),
    )
    _add_workspace_argument(agentic_eval_parser)
    _add_log_level_argument(agentic_eval_parser, default=argparse.SUPPRESS)
    agentic_eval_parser.add_argument(
        "--suite",
        choices=_AGENTIC_EVAL_SUITE_CHOICES,
        default="agentic_benchmark",
        help="Evaluation suite selector (default: agentic_benchmark)",
    )
    agentic_eval_parser.add_argument(
        "--role",
        choices=_AGENTIC_EVAL_ROLE_CHOICES,
        default="all",
        help="Benchmark role scope (default: all)",
    )
    agentic_eval_parser.add_argument(
        "--mode",
        choices=["agentic", "strategy", "context", "all"],
        default="agentic",
        help="Benchmark run mode (default: agentic). 'all' runs agentic+context+strategy sequentially.",
    )
    agentic_eval_parser.add_argument(
        "--matrix-transport",
        choices=("stream", "non_stream"),
        default="stream",
        help=(
            "Transport mode for tool_calling_matrix suite (default: stream). "
            "NOTE: 'both' mode removed to prevent workspace pollution between modes. "
            "Run benchmark twice with different transport modes for parity comparison."
        ),
    )
    agentic_eval_parser.add_argument(
        "--provider-id",
        type=str,
        default="runtime_binding",
        help="Provider identifier for benchmark metadata (default: runtime_binding)",
    )
    agentic_eval_parser.add_argument(
        "--model",
        type=str,
        default="runtime_binding",
        help="Model identifier for benchmark metadata (default: runtime_binding)",
    )
    agentic_eval_parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Limit benchmark to specific case id (repeatable)",
    )
    agentic_eval_parser.add_argument(
        "--levels",
        action="append",
        default=[],
        help=(
            "Limit tool_calling_matrix suite to specific level range (e.g., l1-l3, l4-l9). "
            "Supports: 'l1' or '1' (single), 'l1-l3' or '1-3' (range), "
            "'l1,l3' or '1,3' (multiple). Repeatable. "
            "Example: --levels l1-l3 --levels l7"
        ),
    )
    agentic_eval_parser.add_argument(
        "--max-fixes",
        type=int,
        default=8,
        help="Maximum number of aggregated repair actions in output (default: 8)",
    )
    agentic_eval_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="CLI output format (default: human)",
    )
    agentic_eval_parser.add_argument(
        "--output",
        type=str,
        default="",
        help=(
            "Workspace-relative output JSON path for audit package. "
            "Default: runtime/llm_evaluations/<run_id>/AGENTIC_EVAL_AUDIT.json"
        ),
    )
    agentic_eval_parser.add_argument(
        "--baseline-pull",
        action="append",
        default=[],
        help=(
            "Download external baseline library references before evaluation. "
            "Choices: bfcl, toolbench, all. Repeatable."
        ),
    )
    agentic_eval_parser.add_argument(
        "--baseline-output",
        type=str,
        default="runtime/llm_evaluations/baselines",
        help=(
            "Workspace-relative output directory for baseline pull assets. Default: runtime/llm_evaluations/baselines"
        ),
    )
    agentic_eval_parser.add_argument(
        "--baseline-timeout",
        type=float,
        default=20.0,
        help="Per-file HTTP timeout (seconds) for baseline pull (default: 20.0)",
    )
    agentic_eval_parser.add_argument(
        "--baseline-retries",
        type=int,
        default=2,
        help="HTTP retry count per file for baseline pull (default: 2)",
    )
    agentic_eval_parser.add_argument(
        "--baseline-only",
        action="store_true",
        default=False,
        help="Only run baseline pull and exit (skip benchmark execution).",
    )
    agentic_eval_parser.add_argument(
        "--baseline-cache-check",
        action="store_true",
        default=False,
        help=("Cache detection mode for baseline pull: only verify cached assets, do not perform network download."),
    )
    agentic_eval_parser.add_argument(
        "--baseline-refresh",
        action="store_true",
        default=False,
        help="Force baseline pull to refresh from network and overwrite cache entries.",
    )
    agentic_eval_parser.add_argument(
        "--compare-baseline",
        type=str,
        default="",
        help=(
            "Compare current benchmark run against a baseline audit. "
            "Accepts run_id (under <metadata_dir>/runtime/llm_evaluations/<run_id>/AGENTIC_EVAL_AUDIT.json) "
            "or an explicit audit JSON path."
        ),
    )
    agentic_eval_parser.add_argument(
        "--probe",
        action="store_true",
        default=False,
        help=(
            "Run role LLM accessibility probe before benchmark execution. "
            "Exits non-zero if any role fails, preventing wasted benchmark cycles."
        ),
    )
    agentic_eval_parser.add_argument(
        "--probe-timeout",
        type=float,
        default=30.0,
        help="Per-role probe timeout in seconds (default: 30.0)",
    )
    agentic_eval_parser.add_argument(
        "--observable",
        action="store_true",
        default=False,
        help=(
            "Enable real-time LLM output observability for tool_calling_matrix suite. "
            "Prints thinking process, tool calls, and tool results as they happen."
        ),
    )
    agentic_eval_parser.add_argument(
        "--max-failed",
        type=int,
        default=0,
        help=(
            "Stop benchmark early after N failures are reached (0 = disabled, run all cases). "
            "Useful for rapid iteration: fix failures as soon as threshold is hit."
        ),
    )
    agentic_eval_parser.add_argument(
        "--rerun-failed",
        type=str,
        default="",
        help=(
            "Rerun only the cases that failed in a previous benchmark run. "
            "Accepts a run_id (e.g., 'f6d7bb13') or an explicit audit JSON path. "
            "This avoids re-running passed cases, saving significant time. "
            "Example: --rerun-failed f6d7bb13 or --rerun-failed ./audit.json"
        ),
    )
    agentic_eval_parser.add_argument(
        "--list-failed",
        action="store_true",
        default=False,
        help=(
            "List the failed cases from a previous run without executing them. "
            "Use with --rerun-failed to see which cases will be rerun."
        ),
    )

    # ── probe ───────────────────────────────────────────────────────────────
    probe_parser = subparsers.add_parser(
        "probe",
        help="Probe each role's LLM connectivity (pre-flight check for benchmarks)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Send a minimal message to each role and verify it responds. "
            "Use this to confirm the LLM configuration is working before "
            "running time-consuming benchmark evaluations."
        ),
    )
    _add_workspace_argument(probe_parser)
    _add_log_level_argument(probe_parser, default=argparse.SUPPRESS)
    probe_parser.add_argument(
        "--role",
        action="append",
        default=[],
        help="Role to probe (repeatable). Defaults to all 5 roles (pm, architect, chief_engineer, director, qa).",
    )
    probe_parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-role probe timeout in seconds (default: 30.0)",
    )
    probe_parser.add_argument(
        "--format",
        choices=("human", "json"),
        default="human",
        help="Output format (default: human)",
    )

    # ── ingest ─────────────────────────────────────────────────────────────
    ingest_parser = subparsers.add_parser(
        "ingest",
        help="Ingest documents into the Truth Crucible knowledge pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Run one or more files through the knowledge pipeline. "
            "Supports: markdown, text, PDF, Word, CSV, HTML, Excel, PowerPoint.\n\n"
            "Example:\n"
            "  polaris ingest ./docs --recursive\n"
            "  polaris ingest README.md output.jsonl"
        ),
    )
    _add_workspace_argument(ingest_parser)
    _add_log_level_argument(ingest_parser, default=argparse.SUPPRESS)
    ingest_parser.add_argument(
        "paths",
        nargs="+",
        help="Files or directories to ingest",
    )
    ingest_parser.add_argument(
        "--recursive",
        "-r",
        action="store_true",
        default=False,
        help="Recursively process subdirectories",
    )
    ingest_parser.add_argument(
        "--glob",
        type=str,
        default=None,
        help="Glob pattern to filter files within directories (e.g. '*.md')",
    )
    ingest_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Output file for results (default: stdout)",
    )
    ingest_parser.add_argument(
        "--format",
        choices=["json", "summary", "quiet"],
        default="summary",
        help="Output format (default: summary)",
    )
    ingest_parser.add_argument(
        "--vector-store",
        choices=["jsonl", "lancedb"],
        default="jsonl",
        help="Vector store backend (default: jsonl)",
    )
    ingest_parser.add_argument(
        "--mime-type",
        type=str,
        default=None,
        help="Force MIME type for all files (bypasses auto-detection)",
    )

    # ── sync ────────────────────────────────────────────────────────────────
    sync_parser = subparsers.add_parser(
        "sync",
        help="Synchronize knowledge stores between JSONL and LanceDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Bidirectionally reconcile IdempotentVectorStore (JSONL) and "
            "KnowledgeLanceDB (LanceDB) by content_hash.\n\n"
            "Example:\n"
            "  polaris sync --direction bidirectional\n"
            "  polaris sync --direction jsonl-to-lancedb"
        ),
    )
    _add_workspace_argument(sync_parser)
    _add_log_level_argument(sync_parser, default=argparse.SUPPRESS)
    sync_parser.add_argument(
        "--direction",
        choices=["jsonl-to-lancedb", "lancedb-to-jsonl", "bidirectional"],
        default="bidirectional",
        help="Sync direction (default: bidirectional)",
    )
    sync_parser.add_argument(
        "--format",
        choices=["json", "summary"],
        default="summary",
        help="Output format (default: summary)",
    )
    sync_parser.add_argument(
        "--delete-orphan-lancedb",
        action="store_true",
        default=False,
        help=(
            "When used with lancedb-to-jsonl or bidirectional: "
            "delete LanceDB items that have no corresponding JSONL entry (ghost data cleanup). "
            "Without this flag, LanceDB-only items are imported into JSONL."
        ),
    )

    return parser


# ---------------------------------------------------------------------------
# Workspace + env setup
# ---------------------------------------------------------------------------


def _resolve_workspace(workspace: str) -> Path:
    r"""Resolve workspace path with defensive handling for mangled Windows paths.

    MSYS/Git Bash on Windows strips backslashes from unquoted paths such as
    ``C:\Temp\FileServer``, turning them into ``C:TempFileServer``. This
    function detects that pattern, attempts recovery by probing likely
    absolute-path variants, and raises a clear error when recovery fails.
    """
    original = workspace.strip()
    candidate = Path(original).resolve()

    # On Windows, detect paths that look like mangled absolute paths:
    # e.g. "C:TempFileServer" (drive letter + colon, no path separators).
    if os.name == "nt" and len(original) >= 3:
        drive = original[0]
        if (
            drive.isalpha()
            and original[1] == ":"
            and original[2] not in ("/", "\\")
            and "/" not in original
            and "\\" not in original
        ):
            # Try the original backslash form first (C:\Temp\FileServer)
            recovered = Path(original.replace(":", ":\\", 1))
            if recovered.exists():
                return recovered.resolve()
            # Try a single backslash after the drive (C:\TempFileServer)
            recovered = Path(f"{drive}:\\{original[2:]}")
            if recovered.exists():
                return recovered.resolve()
            # If nothing matches, raise a descriptive error so the user
            # knows the shell mangled their path.
            raise ValueError(
                f"Workspace path appears mangled by the shell: {original!r}. "
                f"Use forward slashes (e.g., {drive}:/temp/fileserver) or quote the path."
            )

    return candidate


def _bind_workspace_env(workspace: Path) -> None:
    os.environ["POLARIS_CONTEXT_ROOT"] = str(workspace)
    if not os.environ.get("POLARIS_RUNTIME_ROOT"):
        os.environ["POLARIS_RUNTIME_ROOT"] = str(Path(workspace) / "runtime")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _get_router() -> CliRouter:
    from polaris.delivery.cli.router import CliRouter

    return CliRouter()


def main(argv: Sequence[str] | None = None) -> int:
    """Canonical entry point for ``python -m polaris.delivery.cli``."""
    _enforce_utf8()
    parser = create_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        configure_cli_logging(getattr(args, "log_level", None))
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    # Normalise workspace
    workspace = _resolve_workspace(str(getattr(args, "workspace", ".") or "."))
    args.workspace = workspace

    # Bind env so downstream services find the workspace
    _bind_workspace_env(workspace)

    # Bootstrap runtime (lazy but early)
    _bootstrap_runtime()

    # Dispatch
    router = _get_router()
    try:
        return router.route(args)
    except KeyboardInterrupt:
        print("\n[polaris] Interrupted", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError) as exc:
        logger.warning("CLI dispatch error: %s", exc)
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
