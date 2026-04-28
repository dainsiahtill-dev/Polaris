"""CLI runner for the CE consumer that polls PENDING_DESIGN and generates blueprints.

Usage:
    python -m polaris.cells.chief_engineer.blueprint.internal.ce_consumer_cli \
        --workspace /path/to/workspace \
        --worker-id ce_worker_01 \
        --poll-interval 5.0

Environment variables:
    KERNELONE_WORKSPACE: Used as workspace if --workspace is not provided.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer

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
    env = os.environ.get("KERNELONE_WORKSPACE", "").strip()
    if env:
        return env
    raise ValueError("workspace is required; set --workspace argument or KERNELONE_WORKSPACE env var")


async def run_once(consumer: CEConsumer, args: argparse.Namespace) -> int:
    """Run a single poll cycle and exit."""
    logger.info("CE consumer (oneshot mode)")
    results = consumer.poll_once()
    logger.info("Processed %d tasks", len(results))
    failed = sum(1 for r in results if not r.get("ok", False))
    if failed:
        logger.warning("%d tasks failed", failed)
    for r in results:
        status = "OK" if r.get("ok") else "FAIL"
        logger.info("  [%s] task_id=%s", status, r.get("task_id", "?"))
    return 0 if failed == 0 else 1


async def run_continuous(consumer: CEConsumer, args: argparse.Namespace) -> int:
    """Run the consumer continuously until interrupted."""
    logger.info("CE consumer (continuous mode) — press Ctrl+C to stop")
    loop = asyncio.get_running_loop()
    consumer_thread_done = asyncio.Event()

    def run_consumer() -> None:
        try:
            consumer.run()
        finally:
            loop.call_soon_threadsafe(consumer_thread_done.set)

    try:
        await asyncio.to_thread(run_consumer)
    except KeyboardInterrupt:
        logger.info("Interrupted, stopping consumer…")
        consumer.stop()
        await asyncio.wait_for(consumer_thread_done.wait(), timeout=10.0)
    return 0


async def main() -> int:
    parser = argparse.ArgumentParser(
        prog="ce_consumer_cli",
        description="ChiefEngineer consumer: poll PENDING_DESIGN, generate blueprints, ack PENDING_EXEC.",
    )
    parser.add_argument(
        "--workspace",
        type=str,
        default=os.environ.get("KERNELONE_WORKSPACE", ""),
        help="Polaris workspace path (default: from env)",
    )
    parser.add_argument(
        "--worker-id",
        type=str,
        default="ce_worker",
        help="Unique worker identifier (default: ce_worker)",
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

    consumer = CEConsumer(
        workspace=workspace,
        worker_id=args.worker_id,
        visibility_timeout_seconds=args.visibility_timeout,
        poll_interval=args.poll_interval,
    )

    if args.mode == "once":
        return await run_once(consumer, args)
    else:
        return await run_continuous(consumer, args)


if __name__ == "__main__":
    try:
        import asyncio

        exit_code: int = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        sys.exit(130)
