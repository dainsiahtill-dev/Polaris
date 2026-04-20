from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

from ..config import resolve_effective_config
from ..verify.orchestrator import run_verify


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentAccel post-hook: run incremental verification")
    parser.add_argument("--project", default=".", help="Project root path")
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Changed files for incremental verify",
    )
    args = parser.parse_args()

    project_dir = Path(os.path.abspath(str(args.project)))
    cfg = resolve_effective_config(project_dir)
    result = run_verify(project_dir=project_dir, config=cfg, changed_files=args.changed_files)
    logger.info("%s", json.dumps(result, ensure_ascii=False))
    raise SystemExit(int(result["exit_code"]))


if __name__ == "__main__":
    main()
