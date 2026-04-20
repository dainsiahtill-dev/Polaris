from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from uuid import uuid4

logger = logging.getLogger(__name__)

from ..config import resolve_effective_config
from ..polaris_paths import resolve_artifact_path
from ..query.context_compiler import compile_context_pack, write_context_pack
from ..storage.cache import ensure_project_dirs, project_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentAccel pre-hook: build context pack under .polaris")
    parser.add_argument("--project", default=".", help="Project root path")
    parser.add_argument("--task", required=True, help="Natural language task")
    parser.add_argument("--out", default="", help="Output file")
    parser.add_argument(
        "--changed-files",
        nargs="*",
        default=[],
        help="Optional changed files",
    )
    parser.add_argument("--hints", nargs="*", default=[], help="Optional path/symbol hints")
    args = parser.parse_args()

    project_dir = Path(os.path.abspath(str(args.project)))
    cfg = resolve_effective_config(project_dir)
    pack = compile_context_pack(
        project_dir=project_dir,
        config=cfg,
        task=args.task,
        changed_files=args.changed_files,
        hints=args.hints,
    )
    accel_home = Path(str(cfg["runtime"]["accel_home"])).resolve()
    paths = project_paths(accel_home, project_dir)
    ensure_project_dirs(paths)
    if args.out:
        out_path = resolve_artifact_path(
            project_dir,
            args.out,
            default_subdir="logs",
            default_name=f"context_pack_pre_hook_{uuid4().hex[:8]}.json",
        )
    else:
        out_path = paths["context"] / f"context_pack_pre_hook_{uuid4().hex[:8]}.json"
    write_context_pack(out_path, pack)
    logger.info("%s", json.dumps({"status": "ok", "out": str(out_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
