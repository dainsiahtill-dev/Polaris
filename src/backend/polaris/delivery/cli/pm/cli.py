"""CLI argument parsing and main entry point for loop-pm."""

import argparse
import logging
import os
import sys
import time
from pathlib import Path


def _bootstrap_backend_import_path():
    """Lazy import of polaris modules after path bootstrap."""
    if __package__:
        # Already in a package, imports should work
        pass
    else:
        # Running as script - ensure backend is in path
        backend_root = Path(__file__).resolve().parents[4]
        backend_root_str = str(backend_root)
        if backend_root_str not in sys.path:
            sys.path.insert(0, backend_root_str)

    from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings
    from polaris.bootstrap.config import get_settings
    from polaris.cells.orchestration.workflow_runtime.public import SUPPORTED_ORCHESTRATION_RUNTIMES
    from polaris.delivery.cli.pm.config import (
        AGENTS_APPROVAL_MODES,
        CANONICAL_PM_TASKS_REL,
        DEFAULT_AGENTS_APPROVAL_MODE,
        DEFAULT_AGENTS_APPROVAL_TIMEOUT,
        DEFAULT_DIRECTOR_SUBPROCESS_LOG,
        PROMPT_PROFILE_ENV,
        enforce_utf8,
    )
    from polaris.delivery.cli.pm.orchestration_core import load_cli_directive, run_architect_docs_stage
    from polaris.delivery.cli.pm.orchestration_engine import ensure_docs_ready, run_once
    from polaris.delivery.cli.pm.utils import read_json_file
    from polaris.kernelone.events import set_dialogue_seq
    from polaris.kernelone.fs.control_flags import pause_flag_path, pause_requested
    from polaris.kernelone.fs.jsonl.ops import flush_jsonl_buffers, scan_last_seq
    from polaris.kernelone.storage import resolve_ramdisk_root, state_to_ramdisk_enabled
    from polaris.kernelone.storage.io_paths import (
        build_cache_root,
        resolve_artifact_path,
        resolve_workspace_path,
    )

    ensure_minimal_kernelone_bindings()
    os.environ.setdefault("KERNELONE_RUNTIME_CACHE_ROOT", str(get_settings().runtime_base))

    return (
        SUPPORTED_ORCHESTRATION_RUNTIMES,
        AGENTS_APPROVAL_MODES,
        CANONICAL_PM_TASKS_REL,
        DEFAULT_AGENTS_APPROVAL_MODE,
        DEFAULT_AGENTS_APPROVAL_TIMEOUT,
        DEFAULT_DIRECTOR_SUBPROCESS_LOG,
        PROMPT_PROFILE_ENV,
        enforce_utf8,
        load_cli_directive,
        run_architect_docs_stage,
        ensure_docs_ready,
        run_once,
        read_json_file,
        build_cache_root,
        flush_jsonl_buffers,
        pause_flag_path,
        pause_requested,
        resolve_artifact_path,
        resolve_ramdisk_root,
        resolve_workspace_path,
        scan_last_seq,
        set_dialogue_seq,
        state_to_ramdisk_enabled,
    )


logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create argument parser for loop-pm."""
    parser = argparse.ArgumentParser(
        prog="loop-pm",
        description="Polaris PM Loop - Project Manager Loop",
    )
    parser.add_argument(
        "--workspace",
        "-w",
        type=str,
        default=os.getcwd(),
        help="Workspace directory",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=1,
        help="Number of iterations",
    )
    parser.add_argument(
        "--agent",
        type=str,
        choices=["pm", "director", "qa", "architect"],
        default="pm",
        help="Agent to run",
    )
    parser.add_argument("--pm-backend", default="auto", help="PM backend adapter")
    parser.add_argument(
        "--model",
        default=os.environ.get("KERNELONE_PM_MODEL") or os.environ.get("KERNELONE_MODEL") or "gpt-4",
        help="PM model name",
    )
    parser.add_argument("--timeout", type=int, default=0, help="PM backend timeout in seconds")
    parser.add_argument("--json-log", default="runtime/events/pm.events.jsonl", help="PM JSONL event log path")
    parser.add_argument("--plan-path", default="runtime/contracts/plan.md", help="Plan artifact path")
    parser.add_argument("--gap-report-path", default="runtime/contracts/gap_report.md", help="Gap report path")
    parser.add_argument("--qa-path", default="runtime/results/qa_report.json", help="QA result path")
    parser.add_argument("--requirements-path", default="runtime/contracts/requirements.md", help="Requirements path")
    parser.add_argument("--pm-out", default="runtime/contracts/pm_tasks.contract.json", help="PM task contract path")
    parser.add_argument("--pm-report", default="runtime/results/pm.report.md", help="PM markdown report path")
    parser.add_argument("--state-path", default="runtime/state/pm.state.json", help="PM state path")
    parser.add_argument(
        "--task-history-path",
        default="runtime/events/pm.task_history.events.jsonl",
        help="PM task history JSONL path",
    )
    parser.add_argument(
        "--director-result-path",
        default="runtime/results/director.result.json",
        help="Director result path",
    )
    parser.add_argument(
        "--director-events-path",
        default="runtime/events/runtime.events.jsonl",
        help="Director/runtime events JSONL path",
    )
    parser.add_argument(
        "--pm-task-path",
        default="runtime/contracts/pm_tasks.contract.json",
        help="PM task contract path passed to Director",
    )
    parser.add_argument("--dialogue-path", default="runtime/events/dialogue.transcript.jsonl")
    parser.add_argument("--pm-last-message-path", default="runtime/results/pm_last.output.md")
    parser.add_argument("--ramdisk-root", default="")
    parser.add_argument("--prompt-profile", default=os.environ.get("KERNELONE_PROMPT_PROFILE", ""))
    parser.add_argument("--agents-approval-mode", default="auto_accept")
    parser.add_argument("--agents-approval-timeout", type=int, default=90)
    parser.add_argument("--orchestration-runtime", default="workflow")
    parser.add_argument("--max-failures", type=int, default=5)
    parser.add_argument("--max-blocked", type=int, default=5)
    parser.add_argument("--max-same-task", type=int, default=3)
    parser.add_argument("--blocked-strategy", default="skip")
    parser.add_argument("--blocked-degrade-max-retries", type=int, default=2)
    parser.add_argument("--pm-show-output", action="store_true", default=False)
    parser.add_argument("--loop", action="store_true", default=False)
    parser.add_argument("--interval", type=int, default=20)
    parser.add_argument("--max-iterations", type=int, default=0)
    parser.add_argument("--resume", action="store_true", default=False)
    parser.add_argument("--run-director", action="store_true", default=False)
    parser.add_argument("--director-path", default="src/backend/polaris/delivery/cli/loop-director.py")
    parser.add_argument("--director-type", default="auto")
    parser.add_argument("--director-model", default="")
    parser.add_argument("--director-timeout", type=int, default=3600)
    parser.add_argument("--director-show-output", action="store_true", default=False)
    parser.add_argument("--director-result-timeout", type=int, default=60)
    parser.add_argument("--director-iterations", type=int, default=1)
    parser.add_argument("--director-workflow-execution-mode", default="parallel")
    parser.add_argument("--director-max-parallel-tasks", type=int, default=3)
    parser.add_argument("--director-ready-timeout-seconds", type=int, default=30)
    parser.add_argument("--director-claim-timeout-seconds", type=int, default=30)
    parser.add_argument("--director-phase-timeout-seconds", type=int, default=900)
    parser.add_argument("--director-complete-timeout-seconds", type=int, default=30)
    parser.add_argument("--director-task-timeout-seconds", type=int, default=86400)
    parser.add_argument("--director-match-mode", default="latest")
    parser.add_argument("--events-path", default="runtime/events/runtime.events.jsonl")
    parser.add_argument("--planner-response-path", default="runtime/results/planner.response.json")
    parser.add_argument("--ollama-response-path", default="runtime/results/ollama.response.json")
    parser.add_argument("--qa-response-path", default="runtime/results/qa.response.json")
    parser.add_argument("--reviewer-response-path", default="runtime/results/reviewer.response.json")
    parser.add_argument("--json-log-path", dest="json_log_path", default="")
    parser.add_argument("--codex-profile", default="")
    parser.set_defaults(codex_full_auto=True)
    parser.add_argument("--codex-full-auto", dest="codex_full_auto", action="store_true")
    parser.add_argument("--no-codex-full-auto", dest="codex_full_auto", action="store_false")
    parser.add_argument("--codex-dangerous", action="store_true", default=False)
    parser.add_argument("--stop-on-failure", action="store_true", default=True)
    parser.add_argument("--no-stop-on-failure", dest="stop_on_failure", action="store_false")
    parser.add_argument("--heartbeat", action="store_true", default=False)
    return parser


def main() -> int:
    """Main entry point."""
    args = sys.argv[1:]
    parser = create_parser()
    parsed = parser.parse_args(args)

    # Use lazy imports
    (
        _,  # SUPPORTED_ORCHESTRATION_RUNTIMES
        _,  # AGENTS_APPROVAL_MODES
        _,  # CANONICAL_PM_TASKS_REL
        _,  # DEFAULT_AGENTS_APPROVAL_MODE
        _,  # DEFAULT_AGENTS_APPROVAL_TIMEOUT
        _,  # DEFAULT_DIRECTOR_SUBPROCESS_LOG
        _,  # PROMPT_PROFILE_ENV
        enforce_utf8,
        _,  # load_cli_directive
        _,  # run_architect_docs_stage
        _,  # ensure_docs_ready
        run_once,
        _,  # read_json_file
        _,  # build_cache_root
        _,  # flush_jsonl_buffers
        _,  # pause_flag_path
        pause_requested,
        _,  # resolve_artifact_path
        _,  # resolve_ramdisk_root
        _,  # resolve_workspace_path
        _,  # scan_last_seq
        _,  # set_dialogue_seq
        _,  # state_to_ramdisk_enabled
    ) = _bootstrap_backend_import_path()

    enforce_utf8()

    workspace = str(parsed.workspace or os.getcwd())
    logger.info("Starting loop-pm in %s", workspace)
    logger.info("Iterations: %d", parsed.iterations)
    logger.info("Agent: %s", parsed.agent)

    if parsed.loop:
        max_iterations = int(parsed.max_iterations or 0)
        if max_iterations <= 0 and int(parsed.iterations or 0) != 1:
            max_iterations = max(0, int(parsed.iterations or 0))
        iteration = 1
        while True:
            if pause_requested(workspace):
                logger.info("PM loop paused by control flag for %s", workspace)
                return 0
            logger.info("Loop iteration %d", iteration)
            code = int(run_once(parsed, iteration=iteration) or 0)
            if code != 0 and parsed.stop_on_failure:
                return code
            if max_iterations > 0 and iteration >= max_iterations:
                return code
            iteration += 1
            time.sleep(max(1, int(parsed.interval or 1)))

    exit_code = 0
    for i in range(max(0, int(parsed.iterations or 0))):
        logger.info("Iteration %d/%d", i + 1, parsed.iterations)
        code = int(run_once(parsed, iteration=i + 1) or 0)
        if code != 0:
            exit_code = code
            if parsed.stop_on_failure:
                return exit_code

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
