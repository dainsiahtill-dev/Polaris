"""CLI runner for the QA consumer that polls PENDING_QA and emits audit verdicts.

Usage:
    python -m polaris.cells.qa.audit_verdict.internal.qa_consumer_cli \
        --workspace /path/to/workspace \
        --worker-id qa_worker_01 \
        --poll-interval 5.0

Environment variables:
    POLARIS_WORKSPACE: Used as workspace if --workspace is not provided.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from polaris.cells.qa.audit_verdict.internal.qa_consumer import QAConsumer

__frozen__ = True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _resolve_workspace(args: argparse.Namespace) -> str:
    """Resolve workspace from CLI arg or environment."""
    if args.workspace:
        return str(args.workspace).strip()
    env = os.environ.get("POLARIS_WORKSPACE", "").strip()
    if env:
        return env
    raise ValueError("workspace is required; set --workspace argument or POLARIS_WORKSPACE env var")


def run_once(consumer: QAConsumer, args: argparse.Namespace) -> int:
    """Run a single poll cycle and exit."""
    logger.info("QA consumer (oneshot mode)")
    results = consumer.poll_once()
    logger.info("Processed %d tasks", len(results))
    failed = sum(1 for r in results if not r.get("ok", False))
    if failed:
        logger.warning("%d tasks failed", failed)
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        verdict = r.get("verdict", "?")
        logger.info("  [%s] task_id=%s verdict=%s", status, r.get("task_id", "?"), verdict)
    return 0 if failed == 0 else 1


def run_continuous(consumer: QAConsumer, args: argparse.Namespace) -> int:
    """Run the consumer continuously until interrupted."""
    logger.info("QA consumer (continuous mode) — press Ctrl+C to stop")
    try:
        consumer.run()
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping consumer…")
        consumer.stop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="qa_consumer_cli",
        description="QA consumer: poll PENDING_QA, run audits, emit verdicts.",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=os.environ.get("POLARIS_WORKSPACE", ""),
        help="Polaris workspace path (default: from env)",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default="qa_worker",
        help="Unique worker identifier (default: qa_worker)",
    )
    parser.add_argument(
        "--visibility-timeout",
        type=int,
        default=900,
        help="Task lease visibility timeout in seconds (default: 900)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=5.0,
        help="Seconds between poll cycles when no task found (default: 5.0)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices={"once", "continuous"},
        default="continuous",
        help="Run once or continuously (default: continuous)",
    )

    args = parser.parse_args()

    try:
        workspace = _resolve_workspace(args)
    except ValueError as exc:
        parser.error(str(exc))
        return 1  # unreachable

    consumer = QAConsumer(
        workspace=workspace,
        worker_id=args.worker_id,
        visibility_timeout_seconds=args.visibility_timeout,
        poll_interval=args.poll_interval,
    )

    if args.mode == "once":
        return run_once(consumer, args)
    else:
        return run_continuous(consumer, args)


if __name__ == "__main__":
    try:
        exit_code: int = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
