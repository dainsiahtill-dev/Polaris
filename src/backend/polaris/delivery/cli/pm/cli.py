"""CLI argument parsing and main entry point for loop-pm."""

import argparse
import logging
import os
import sys
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
    from polaris.infrastructure.compat.io_utils import (
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
    return parser


def main() -> int:
    """Main entry point."""
    args = sys.argv[1:]
    parser = create_parser()
    parsed = parser.parse_args(args)

    workspace = str(parsed.workspace or os.getcwd())
    logger.info("Starting loop-pm in %s", workspace)
    logger.info("Iterations: %d", parsed.iterations)
    logger.info("Agent: %s", parsed.agent)

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
        _,  # pause_requested
        _,  # resolve_artifact_path
        _,  # resolve_ramdisk_root
        _,  # resolve_workspace_path
        _,  # scan_last_seq
        _,  # set_dialogue_seq
        _,  # state_to_ramdisk_enabled
    ) = _bootstrap_backend_import_path()

    enforce_utf8()

    # Run iterations
    for i in range(parsed.iterations):
        logger.info("Iteration %d/%d", i + 1, parsed.iterations)
        run_once(workspace)

    return 0


if __name__ == "__main__":
    sys.exit(main())
