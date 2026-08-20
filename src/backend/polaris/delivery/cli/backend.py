"""Canonical backend serve CLI for Polaris instances."""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _runtime_root_for_args(args: argparse.Namespace) -> str:
    """Resolve one backend process to its canonical runtime identity."""

    workspace = Path(args.workspace).expanduser().resolve() if args.workspace else Path.cwd().resolve()
    requested = Path(args.runtime_root).expanduser().resolve() if args.runtime_root else None
    from polaris.cells.storage.layout.public import canonical_project_runtime_root

    canonical = Path(canonical_project_runtime_root(str(workspace))).resolve()
    # ``--runtime-root=<workspace>`` and ``<workspace>/runtime`` are legacy
    # launcher claims, never explicit external-storage opt-ins.  Normalize
    # both to the project-local authority so an old long-lived Launcher cannot
    # make a fresh child process revive a split runtime identity.
    if requested is None or requested in {workspace, workspace / "runtime"}:
        return str(canonical)
    return str(requested)


def _apply_env(args: argparse.Namespace) -> None:
    if args.workspace:
        workspace = str(Path(args.workspace).expanduser().resolve())
        os.environ["KERNELONE_WORKSPACE"] = workspace
        # Single-backend/single-workspace is a process-startup invariant.  Keep
        # an immutable binding separate from KERNELONE_WORKSPACE, which legacy
        # settings synchronization may still update at runtime.
        os.environ["KERNELONE_INSTANCE_WORKSPACE"] = workspace
    runtime_root = _runtime_root_for_args(args)
    os.environ["KERNELONE_RUNTIME_ROOT"] = runtime_root
    # A resolved project runtime is not a shared cache base.
    os.environ.pop("KERNELONE_RUNTIME_CACHE_ROOT", None)
    if args.token:
        os.environ["KERNELONE_TOKEN"] = args.token
    os.environ["KERNELONE_BACKEND_PORT"] = str(args.port)
    if args.instance_id:
        os.environ["KERNELONE_INSTANCE_ID"] = str(args.instance_id)
    if args.kind:
        os.environ["KERNELONE_INSTANCE_KIND"] = str(args.kind)
    if args.cors_origins:
        os.environ["KERNELONE_CORS_ORIGINS"] = args.cors_origins


def _find_polaris_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "package.json").is_file() and (parent / "src/backend/polaris").is_dir():
            return parent
    return start


def _register_instance(args: argparse.Namespace) -> None:
    if not args.register_instance:
        return
    from polaris.cells.instances.internal.service import (
        InstanceRecord,
        InstanceRegistry,
        sanitize_instance_id,
        utc_timestamp,
    )

    workspace = str(Path(args.workspace).expanduser().resolve()) if args.workspace else str(Path.cwd())
    polaris_root = str(_find_polaris_root(Path.cwd()).resolve())
    runtime_root = _runtime_root_for_args(args)
    instance_id = sanitize_instance_id(args.instance_id or args.instance_name or Path(workspace).name)
    frontend_port = int(args.frontend_port or 0)
    record = InstanceRecord(
        instance_id=instance_id,
        name=args.instance_name or Path(workspace).name or instance_id,
        kind=args.kind or "project",
        polaris_root=polaris_root,
        workspace=workspace,
        runtime_root=runtime_root,
        backend_port=int(args.port),
        frontend_port=frontend_port,
        backend_url=f"http://{args.host}:{int(args.port)}",
        frontend_url=f"http://{args.host}:{frontend_port}" if frontend_port > 0 else "",
        token=args.token or os.environ.get("KERNELONE_TOKEN", ""),
        backend_reload=bool(args.reload),
        frontend_vite=False,
        start_frontend=False,
        status="running",
        backend_pid=os.getpid(),
        frontend_pid=None,
        last_started_at=utc_timestamp(),
        metadata={"registered_by": "backend_cli"},
    )
    InstanceRegistry().save(record)


def serve(args: argparse.Namespace) -> int:
    _apply_env(args)
    _register_instance(args)
    import uvicorn

    reload_dirs = [str(Path(args.reload_dir).expanduser().resolve())] if args.reload and args.reload_dir else None
    uvicorn.run(
        "polaris.delivery.http.app_factory:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=bool(args.reload),
        reload_dirs=reload_dirs,
        log_level=args.log_level,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Polaris backend CLI")
    sub = parser.add_subparsers(dest="command")

    serve_parser = sub.add_parser("serve", description="Serve one Polaris backend instance")
    serve_parser.add_argument("--workspace", default="", help="Single workspace bound to this backend instance")
    serve_parser.add_argument("--runtime-root", default="", help="Instance-specific runtime root")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=49977)
    serve_parser.add_argument("--token", default="")
    serve_parser.add_argument("--reload", action="store_true")
    serve_parser.add_argument("--reload-dir", default=str(Path.cwd()))
    serve_parser.add_argument("--cors-origins", default="")
    serve_parser.add_argument("--log-level", default="info")
    serve_parser.add_argument("--register-instance", action="store_true")
    serve_parser.add_argument("--instance-id", default="")
    serve_parser.add_argument("--instance-name", default="")
    serve_parser.add_argument("--kind", default="project")
    serve_parser.add_argument("--frontend-port", type=int, default=0)
    serve_parser.set_defaults(func=serve)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 2
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
