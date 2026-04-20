"""CLI entrypoint for refreshing projection back-mapping and impact reports."""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from polaris.cells.factory.pipeline.public import ProjectionChangeAnalysisService
from polaris.cells.factory.pipeline.public.contracts import RefreshProjectionBackMappingCommandV1
from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs import set_default_adapter

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh back-mapping artifacts for a generated projection experiment.",
    )
    parser.add_argument("--workspace", required=True, help="Workspace root that contains the experiment artifacts")
    parser.add_argument("--experiment-id", required=True, help="Projection experiment identifier")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    workspace = str(Path(args.workspace).resolve())
    set_default_adapter(LocalFileSystemAdapter())

    service = ProjectionChangeAnalysisService(workspace)
    result = service.refresh_back_mapping_result(
        RefreshProjectionBackMappingCommandV1(
            workspace=workspace,
            experiment_id=str(args.experiment_id),
        )
    )
    logger.info(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
