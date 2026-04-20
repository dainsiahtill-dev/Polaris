"""CLI entrypoint for selective projection reprojection."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from polaris.cells.factory.pipeline.public import FactoryProjectionLabService
from polaris.cells.factory.pipeline.public.contracts import ReprojectProjectionExperimentCommandV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import set_default_adapter

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reproject an existing experiment after a requirement change.",
    )
    parser.add_argument("--workspace", required=True, help="Workspace root that contains the experiment artifacts")
    parser.add_argument("--experiment-id", required=True, help="Projection experiment identifier")
    parser.add_argument("--requirement", required=True, help="Updated natural-language requirement")
    parser.add_argument(
        "--disable-pm-llm",
        action="store_true",
        help="Disable PM-bound LLM normalization and use deterministic fallback",
    )
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip generated project verification after reprojection",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = str(Path(args.workspace).resolve())
    set_default_adapter(LocalFileSystemAdapter())

    service = FactoryProjectionLabService(workspace)
    result = service.reproject_experiment(
        ReprojectProjectionExperimentCommandV1(
            workspace=workspace,
            experiment_id=str(args.experiment_id),
            requirement=str(args.requirement or ""),
            use_pm_llm=not bool(args.disable_pm_llm),
            run_verification=not bool(args.skip_verification),
        )
    )
    logger.info(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
