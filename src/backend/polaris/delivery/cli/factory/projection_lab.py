"""CLI entrypoint for the factory projection experiment."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from polaris.cells.factory.pipeline.public import FactoryProjectionLabService
from polaris.cells.factory.pipeline.public.contracts import RunProjectionExperimentCommandV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import set_default_adapter

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a controlled experiment project from Cell IR projection metadata."
    )
    parser.add_argument("--workspace", required=True, help="Workspace root where experiments/ will be created")
    parser.add_argument("--requirement", required=True, help="Natural-language requirement text for the experiment")
    parser.add_argument("--scenario", default="record_cli_app", help="Controlled experiment scenario id")
    parser.add_argument("--project-slug", default="projection_lab", help="Visible project directory slug")
    parser.add_argument(
        "--disable-pm-llm",
        action="store_true",
        help="Disable PM-bound LLM requirement normalization and use deterministic fallback only",
    )
    parser.add_argument("--overwrite", action="store_true", help="Allow overwriting an existing experiment directory")
    parser.add_argument(
        "--skip-verification",
        action="store_true",
        help="Skip post-generation verification commands",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = str(Path(args.workspace).resolve())
    set_default_adapter(LocalFileSystemAdapter())

    service = FactoryProjectionLabService(workspace)
    result = service.run_projection_experiment(
        RunProjectionExperimentCommandV1(
            workspace=workspace,
            scenario_id=str(args.scenario or "record_cli_app"),
            requirement=str(args.requirement or ""),
            project_slug=str(args.project_slug or "projection_lab"),
            use_pm_llm=not bool(args.disable_pm_llm),
            run_verification=not bool(args.skip_verification),
            overwrite=bool(args.overwrite),
        )
    )
    logger.info(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
