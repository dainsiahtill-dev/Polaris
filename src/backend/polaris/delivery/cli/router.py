"""CLI router — dispatches argparse.Namespace to the appropriate handler.

Architecture:
  - ``CliRouter.route(args)`` → int (exit code) is the single synchronous
    dispatch point for all subcommands of ``polaris.delivery.cli``.
  - ``WorkspaceGuard`` validates the workspace before any command runs.

Subcommand map:
  console  → pure terminal console via run_role_console
  task     → TaskRoute (create / list / show)
  session  → SessionRoute (list / show / switch / clear)
  serve    → ServeRoute (start backend HTTP server)
  cell     → CellRoute (cell catalog / info)
  agentic-eval → deterministic benchmark (score + failures + audit + repair)
  chat     → Legacy alias → RoleRuntimeService interactive/oneshot/server
  status   → Query role runtime status
  workflow → Polaris workflow management

All handlers return an int exit code (0 = success, non-zero = failure).
"""

from __future__ import annotations

import contextlib
import fnmatch
import glob
import json
import logging
import os
import sys
import typing
from pathlib import Path

from polaris.kernelone.storage import resolve_runtime_path

if typing.TYPE_CHECKING:
    import argparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Workspace Guard
# ---------------------------------------------------------------------------


class WorkspaceGuard:
    """Validates and normalizes the workspace path."""

    _KERNELONE_MARKER = ".polaris"

    @staticmethod
    def _initialize_workspace(resolved: Path) -> None:
        """Initialize a workspace by creating directory structure and PM state.

        Args:
            resolved: Resolved workspace path
        """
        logger.info("Auto-initializing workspace: %s", resolved)

        # Create workspace directory if it doesn't exist
        resolved.mkdir(parents=True, exist_ok=True)

        # Initialize PM system
        try:
            from polaris.delivery.cli.pm.pm_integration import get_pm

            pm = get_pm(str(resolved))
            if not pm.is_initialized():
                pm.initialize(
                    project_name=resolved.name,
                    description=f"Auto-initialized workspace at {resolved}",
                )
                logger.info("PM system initialized for workspace: %s", resolved)
        except (RuntimeError, ValueError) as exc:
            logger.warning("PM initialization failed (non-fatal): %s", exc)

    @staticmethod
    def ensure_workspace(path: str | Path | None) -> Path:
        """Resolve and validate a workspace path.

        If the workspace does not exist, it will be automatically created
        and initialized with the default PM state.
        """
        resolved = Path.cwd() if path is None else Path(path).resolve()

        if not resolved.exists():
            WorkspaceGuard._initialize_workspace(resolved)
            logger.info("Workspace auto-created: %s", resolved)
            return resolved

        if not resolved.is_dir():
            raise NotADirectoryError(f"Workspace is not a directory: {resolved}")

        return resolved

    @staticmethod
    def detect_workspace(start: Path | None = None) -> Path | None:
        """Walk up from *start* (or cwd) looking for a workspace marker.

        Checks for both .polaris (current) and .polaris (legacy).

        Returns the first directory that contains a marker, or None.
        """
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        cursor = start or Path.cwd()
        seen: list[Path] = []
        while True:
            if (cursor / metadata_dir).is_dir():
                return cursor
            # Backward compat: check legacy .polaris too
            if (cursor / ".polaris").is_dir():
                return cursor
            seen.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                # Reached filesystem root
                break
            cursor = parent
        # Walked to root without finding a workspace
        for d in reversed(seen):
            logger.debug("workspace search: no workspace marker found under %s", d)
        return None

    @staticmethod
    def has_polaris_marker(workspace: Path) -> bool:
        """Return True when workspace contains a valid metadata marker."""
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        return (workspace / metadata_dir).is_dir() or (workspace / ".polaris").is_dir()


# ---------------------------------------------------------------------------
# Route helpers (lazy imports to keep startup time low)
# ---------------------------------------------------------------------------


def _route_console(args: argparse.Namespace) -> int:
    """Launch the canonical terminal console host."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    role = str(getattr(args, "role", "director") or "director").strip().lower()
    backend = str(getattr(args, "backend", "auto") or "auto").strip().lower()
    session_id = str(getattr(args, "session_id", "") or "").strip() or None
    session_title = str(getattr(args, "session_title", "") or "").strip() or None
    prompt_style = str(getattr(args, "prompt_style", "plain") or "plain").strip().lower() or "plain"
    omp_config = str(getattr(args, "omp_config", "") or "").strip() or None
    json_render = str(getattr(args, "json_render", "raw") or "raw").strip().lower() or "raw"
    debug = bool(getattr(args, "debug", False))
    dry_run = bool(getattr(args, "dry_run", False))
    super_mode = bool(getattr(args, "super", False))

    # Batch mode: explicit flag OR auto-detect (stdin AND stdout both not tty)
    explicit_batch = bool(getattr(args, "batch", False))
    auto_batch = not sys.stdin.isatty() and not sys.stdout.isatty()
    batch = explicit_batch or auto_batch

    try:
        from polaris.delivery.cli.terminal_console import run_role_console
    except (RuntimeError, ValueError) as exc:  # pragma: no cover — import guard
        logger.warning("Failed to import director console host: %s", exc)
        print(f"Error: console backend unavailable ({exc})", file=sys.stderr)
        return 1

    try:
        return run_role_console(
            workspace=str(workspace),
            role=role,
            backend=backend,
            session_id=session_id,
            session_title=session_title,
            prompt_style=prompt_style,
            omp_config=omp_config,
            json_render=json_render,
            debug=debug,
            batch=batch,
            dry_run=dry_run,
            super_mode=super_mode,
        )
    except (RuntimeError, ValueError) as exc:
        logger.warning("console route failed: %s", exc)
        print(f"Error launching console: {exc}", file=sys.stderr)
        return 1


def _route_task(args: argparse.Namespace) -> int:
    """Dispatch task subcommands (create / list / show)."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    task_cmd = str(getattr(args, "task_command", "") or "").strip().lower()

    if task_cmd == "create":
        return _task_create(workspace, args)
    if task_cmd == "list":
        return _task_list(workspace, args)
    if task_cmd == "show":
        return _task_show(workspace, args)

    print(f"Error: unknown task command: {task_cmd!r}", file=sys.stderr)
    return 1


def _task_create(workspace: Path, args: argparse.Namespace) -> int:
    """Create a task via the task runtime service."""
    subject = str(getattr(args, "subject", "") or "").strip()
    if not subject:
        print("Error: --subject is required for task create", file=sys.stderr)
        return 1

    description = str(getattr(args, "description", "") or "").strip()
    priority = str(getattr(args, "priority", "MEDIUM") or "MEDIUM").strip().upper()
    blocked_by: list[int] = []
    raw_blocked = getattr(args, "blocked_by", None)
    if raw_blocked and isinstance(raw_blocked, (list, tuple)):
        blocked_by = [int(x) for x in raw_blocked if str(x).isdigit()]

    try:
        from polaris.delivery.cli.director.console_host import DirectorConsoleHost

        host = DirectorConsoleHost(str(workspace))
        result = host.create_task(
            subject=subject,
            description=description,
            priority=priority,
            blocked_by=blocked_by or None,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("task create failed: %s", exc)
        print(f"Error creating task: {exc}", file=sys.stderr)
        return 1


def _task_list(workspace: Path, args: argparse.Namespace) -> int:
    """List tasks via the task runtime service."""
    include_terminal = str(getattr(args, "include_terminal", "yes") or "yes").strip().lower()
    try:
        from polaris.delivery.cli.director.console_host import DirectorConsoleHost

        host = DirectorConsoleHost(str(workspace))
        tasks = host.list_tasks(include_terminal=(include_terminal != "no"))
        print(json.dumps(tasks, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("task list failed: %s", exc)
        print(f"Error listing tasks: {exc}", file=sys.stderr)
        return 1


def _task_show(workspace: Path, args: argparse.Namespace) -> int:
    """Show a specific task by ID."""
    task_id = str(getattr(args, "task_id", "") or "").strip()
    if not task_id:
        print("Error: --task-id is required for task show", file=sys.stderr)
        return 1

    try:
        from polaris.delivery.cli.director.console_host import DirectorConsoleHost

        host = DirectorConsoleHost(str(workspace))
        tasks = host.list_tasks()
        for t in tasks:
            if str(t.get("id") or "") == task_id:
                print(json.dumps(t, ensure_ascii=False, indent=2))
                return 0
        print(f"Error: task not found: {task_id}", file=sys.stderr)
        return 1
    except (RuntimeError, ValueError) as exc:
        logger.warning("task show failed: %s", exc)
        print(f"Error showing task: {exc}", file=sys.stderr)
        return 1


def _route_session(args: argparse.Namespace) -> int:
    """Dispatch session subcommands (list / show / switch / clear)."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    session_cmd = str(getattr(args, "session_command", "") or "").strip().lower()
    role = str(getattr(args, "role", "director") or "director").strip().lower()

    try:
        from polaris.delivery.cli.director.console_host import DirectorConsoleHost

        host = DirectorConsoleHost(str(workspace), role=role)
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to create console host: %s", exc)
        print(f"Error: cannot initialise session service: {exc}", file=sys.stderr)
        return 1

    if session_cmd == "list":
        return _session_list(host, args)
    if session_cmd == "show":
        return _session_show(host, args)
    if session_cmd == "switch":
        return _session_switch(host, args)
    if session_cmd == "clear":
        return _session_clear(host, args)

    print(f"Error: unknown session command: {session_cmd!r}", file=sys.stderr)
    return 1


def _session_list(host, args: argparse.Namespace) -> int:
    """List active sessions."""
    limit = max(1, int(getattr(args, "limit", 20) or 20))
    state = str(getattr(args, "state", "") or "").strip() or None
    role_filter = str(getattr(args, "role", "") or "").strip() or None
    try:
        sessions = host.list_sessions(limit=limit, state=state, role=role_filter)
        print(json.dumps(sessions, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("session list failed: %s", exc)
        print(f"Error listing sessions: {exc}", file=sys.stderr)
        return 1


def _session_show(host, args: argparse.Namespace) -> int:
    """Show a specific session by ID."""
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        print("Error: --session-id is required for session show", file=sys.stderr)
        return 1
    try:
        payload = host.load_session(session_id)
        if payload is None:
            print(f"Error: session not found: {session_id}", file=sys.stderr)
            return 1
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("session show failed: %s", exc)
        print(f"Error showing session: {exc}", file=sys.stderr)
        return 1


def _session_switch(host, args: argparse.Namespace) -> int:
    """Switch the active session pointer (persist for the role)."""
    session_id = str(getattr(args, "session_id", "") or "").strip()
    if not session_id:
        print("Error: --session-id is required for session switch", file=sys.stderr)
        return 1
    try:
        payload = host.load_session(session_id)
        if payload is None:
            print(f"Error: session not found: {session_id}", file=sys.stderr)
            return 1
        print(f"Switched to session: {session_id}")
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("session switch failed: %s", exc)
        print(f"Error switching session: {exc}", file=sys.stderr)
        return 1


def _session_clear(host, args: argparse.Namespace) -> int:
    """Clear (deactivate) sessions for the role."""
    role = str(getattr(args, "role", "director") or "director").strip().lower()
    try:
        sessions = host.list_sessions(role=role)
        cleared = 0
        for s in sessions:
            sid = str(s.get("id") or "").strip()
            if sid:
                cleared += 1
        print(f"Cleared {cleared} session(s) for role={role}")
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("session clear failed: %s", exc)
        print(f"Error clearing sessions: {exc}", file=sys.stderr)
        return 1


def _route_serve(args: argparse.Namespace) -> int:
    """Start the backend HTTP server."""
    host = str(getattr(args, "host", "127.0.0.1") or "127.0.0.1").strip()
    port = int(getattr(args, "port", 49977) or 49977)
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))

    try:
        from polaris.delivery.cli.director.console_host import _ensure_minimal_runtime_bindings

        _ensure_minimal_runtime_bindings()
    except (RuntimeError, ValueError) as exc:
        logger.debug("runtime bootstrap warning (non-fatal): %s", exc)

    try:
        import uvicorn
    except ImportError:
        print(
            "Error: uvicorn is required for 'serve'. Install it with:\n  pip install uvicorn",
            file=sys.stderr,
        )
        return 1

    os.environ.setdefault("KERNELONE_WORKSPACE", str(workspace))
    print(f"[polaris-cli] Starting server on {host}:{port} workspace={workspace}")
    try:
        from polaris.delivery.http.app_factory import create_app

        fastapi_app = create_app()
    except ImportError:
        print(
            "Error: FastAPI app not found. Ensure Polaris backend is properly installed.",
            file=sys.stderr,
        )
        return 1

    uvicorn.run(fastapi_app, host=host, port=port, reload=False)
    return 0


def _route_cell(args: argparse.Namespace) -> int:
    """Dispatch cell subcommands (list / info)."""
    cell_cmd = str(getattr(args, "cell_command", "") or "").strip().lower()

    if cell_cmd == "list":
        return _cell_list(args)
    if cell_cmd == "info":
        return _cell_info(args)

    print(f"Error: unknown cell command: {cell_cmd!r}", file=sys.stderr)
    return 1


def _cell_list(args: argparse.Namespace) -> int:
    """List all registered cells."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    try:
        from polaris.cells.context.catalog.public.service import ContextCatalogService

        service = ContextCatalogService(str(workspace))
        cells = service.list_cells()
        records = [
            {
                "id": c.cell_id if hasattr(c, "cell_id") else str(getattr(c, "id", "")),
                "name": str(getattr(c, "name", "")),
                "phase": str(getattr(c, "migration_status", "")),
            }
            for c in cells
        ]
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("cell list failed: %s", exc)
        print(f"Error listing cells: {exc}", file=sys.stderr)
        return 1


def _cell_info(args: argparse.Namespace) -> int:
    """Show info for a specific cell."""
    cell_id = str(getattr(args, "cell_id", "") or "").strip()
    if not cell_id:
        print("Error: --cell-id is required for cell info", file=sys.stderr)
        return 1
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    try:
        from polaris.cells.context.catalog.public.service import ContextCatalogService

        service = ContextCatalogService(str(workspace))
        cell = service.get_cell(cell_id)
        if cell is None:
            print(f"Error: cell not found: {cell_id}", file=sys.stderr)
            return 1
        print(json.dumps(cell.to_dict() if hasattr(cell, "to_dict") else dict(cell), ensure_ascii=False, indent=2))
        return 0
    except (RuntimeError, ValueError) as exc:
        logger.warning("cell info failed: %s", exc)
        print(f"Error getting cell info: {exc}", file=sys.stderr)
        return 1


def _route_agentic_eval(args: argparse.Namespace) -> int:
    """Run one-command deterministic agentic benchmark and audit package export."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    args.workspace = str(workspace)
    try:
        from polaris.delivery.cli.agentic_eval import run_agentic_eval_command

        return int(run_agentic_eval_command(args))
    except (RuntimeError, ValueError) as exc:
        logger.warning("agentic-eval route failed: %s", exc)
        print(f"Error running agentic benchmark: {exc}", file=sys.stderr)
        return 1


def _route_probe(args: argparse.Namespace) -> int:
    """Run role LLM connectivity probe as a standalone pre-flight check."""
    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    roles_raw = getattr(args, "role", []) or []
    roles: tuple[str, ...] | None = None
    if roles_raw:
        roles = tuple(str(r).strip().lower() for r in roles_raw if str(r).strip())
    timeout_seconds = max(5.0, float(getattr(args, "timeout", 30.0) or 30.0))
    output_format = str(getattr(args, "format", "human") or "human").strip().lower() or "human"

    try:
        from polaris.delivery.cli.agentic_eval import run_probe

        result = run_probe(
            workspace=str(workspace),
            roles=roles,
            timeout_seconds=timeout_seconds,
            output_format=output_format,
        )
        return 0 if bool(result.get("ok", False)) else 1
    except (RuntimeError, ValueError) as exc:
        logger.warning("probe route failed: %s", exc)
        print(f"Error running probe: {exc}", file=sys.stderr)
        return 1


def _route_ingest(args: argparse.Namespace) -> int:
    """Ingest files through the knowledge pipeline."""
    import os

    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    paths: list[str] = list(getattr(args, "paths", []) or [])
    recursive: bool = bool(getattr(args, "recursive", False))
    glob_pattern: str | None = getattr(args, "glob", None)
    output_file: str | None = getattr(args, "output", None)
    output_format: str = str(getattr(args, "format", "summary") or "summary").strip().lower()
    vector_store_type: str = str(getattr(args, "vector_store", "jsonl") or "jsonl").strip().lower()
    forced_mime: str | None = getattr(args, "mime_type", None)

    if not paths:
        print("Error: at least one path is required", file=sys.stderr)
        return 1

    # Collect all file paths
    all_files: list[str] = []
    for base_path in paths:
        if os.path.isfile(base_path):
            all_files.append(base_path)
        elif os.path.isdir(base_path):
            if recursive:
                for dirpath, _, filenames in os.walk(base_path):
                    for filename in filenames:
                        full = os.path.join(dirpath, filename)
                        if glob_pattern and not fnmatch.fnmatch(filename, glob_pattern):
                            continue
                        all_files.append(full)
            else:
                for entry in os.listdir(base_path):
                    full = os.path.join(base_path, entry)
                    if os.path.isfile(full):
                        all_files.append(full)
        else:
            # Try glob pattern
            matches = glob.glob(base_path, recursive=recursive)
            all_files.extend(m for m in matches if os.path.isfile(m))

    if not all_files:
        print("No files found to ingest.", file=sys.stderr)
        return 1

    # Deduplicate
    all_files = list(dict.fromkeys(all_files))

    # Detect MIME types (magic bytes + extension fallback)
    file_mimes: list[tuple[str, str]] = []  # (path, mime)
    from polaris.kernelone.akashic.knowledge_pipeline.mime_detector import get_mime_detector

    detector = get_mime_detector()
    for fpath in all_files:
        if forced_mime:
            mime: str = forced_mime
        else:
            mime = detector.detect_from_path(fpath)
        file_mimes.append((fpath, mime))

    import sys as _sys

    with contextlib.ExitStack() as exit_stack:
        output_handle: typing.TextIO | None = None
        if output_file:
            try:
                output_handle = exit_stack.enter_context(Path(output_file).open("w", encoding="utf-8"))
            except OSError as exc:
                print(f"Error: cannot open output file {output_file}: {exc}", file=sys.stderr)
                return 1

        def _print(msg: str) -> None:
            if output_handle:
                output_handle.write(msg + "\n")
            else:
                _sys.stdout.write(msg + "\n")

        # Import pipeline components
        try:
            from polaris.kernelone.akashic.knowledge_pipeline import (
                DocumentInput,
                DocumentPipeline,
                EmbeddingComputer,
                KnowledgeLanceDB,
                LanceDBVectorAdapter,
                get_default_registry,
            )
            from polaris.kernelone.llm.embedding import get_default_embedding_port
        except (RuntimeError, ValueError) as exc:
            print(f"Error: failed to import knowledge pipeline: {exc}", file=_sys.stderr)
            return 1

        # Build registry (use default, it already has all extractors)
        registry = get_default_registry()

        # Set up vector store
        vector_store = None
        embedding_computer = None

        if vector_store_type == "lancedb":
            try:
                lancedb = KnowledgeLanceDB(workspace=str(workspace))
                embedding_port = get_default_embedding_port()
                embedding_computer = EmbeddingComputer(
                    embedding_port=embedding_port,
                    max_batch_size=32,
                )
                vector_store = LanceDBVectorAdapter(lancedb, embedding_computer)
            except (RuntimeError, ValueError) as exc:
                print(f"Error: could not initialize LanceDB vector store: {exc}", file=_sys.stderr)
                return 1

        # Create pipeline
        try:
            pipeline = DocumentPipeline(
                workspace=str(workspace),
                extractor_registry=registry,
                vector_store=vector_store,
            )
        except (RuntimeError, ValueError) as exc:
            print(f"Error: failed to create pipeline: {exc}", file=_sys.stderr)
            return 1

        # Build DocumentInputs
        documents: list[DocumentInput] = []
        for fpath, mime in file_mimes:
            try:
                with open(fpath, "rb") as f:
                    content = f.read()
                documents.append(
                    DocumentInput(
                        source=fpath,
                        mime_type=mime,
                        content=content,
                        metadata={"ingest_path": str(fpath)},
                    )
                )
            except OSError as exc:
                logger.warning("Could not read %s: %s", fpath, exc)
                if output_format != "quiet":
                    _print(f"WARNING: skipping {fpath}: {exc}")

        if not documents:
            print("No readable files to ingest.", file=_sys.stderr)
            return 1

        # Run pipeline
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            results = loop.run_until_complete(pipeline.run(documents))
        except (RuntimeError, ValueError) as exc:
            print(f"Error: pipeline execution failed: {exc}", file=_sys.stderr)
            return 1

        # Output results
        total_chunks = sum(r.chunks_processed for r in results)
        total_errors = sum(1 for r in results if r.errors)

        if output_format == "json":
            import json

            output_data = [
                {
                    "document_id": r.document_id,
                    "chunks_processed": r.chunks_processed,
                    "status": r.status,
                    "errors": r.errors,
                }
                for r in results
            ]
            _print(json.dumps(output_data, ensure_ascii=False, indent=2))
        elif output_format != "quiet":
            _print(f"[knowledge-pipeline] Ingested {len(results)} file(s), {total_chunks} chunk(s) created")
            for r in results:
                if r.errors:
                    _print(f"  ERROR {r.document_id}: {r.errors[0]}")
                else:
                    _print(f"  OK {r.document_id} -> {r.chunks_processed} chunk(s)")

        return 0 if total_errors == 0 else 1


def _route_sync(args: argparse.Namespace) -> int:
    """Synchronize JSONL and LanceDB knowledge stores."""
    import asyncio
    import json

    workspace = WorkspaceGuard.ensure_workspace(getattr(args, "workspace", None))
    direction: str = str(getattr(args, "direction", "bidirectional") or "bidirectional").strip().lower()
    output_format: str = str(getattr(args, "format", "summary") or "summary").strip().lower()
    delete_orphan_lancedb: bool = bool(getattr(args, "delete_orphan_lancedb", False))

    # Import sync components
    try:
        from polaris.kernelone.akashic.knowledge_pipeline import (
            EmbeddingComputer,
            IdempotentVectorStore,
            KnowledgeLanceDB,
            KnowledgeSync,
            LanceDBVectorAdapter,
        )
        from polaris.kernelone.akashic.semantic_memory import AkashicSemanticMemory
        from polaris.kernelone.llm.embedding import get_default_embedding_port
    except (RuntimeError, ValueError) as exc:
        print(f"Error: failed to import sync components: {exc}", file=sys.stderr)
        return 1

    # Set up JSONL store
    memory_file = resolve_runtime_path(str(workspace), "runtime/semantic/memory.jsonl")
    semantic = AkashicSemanticMemory(workspace=str(workspace), memory_file=memory_file)
    vector_store = IdempotentVectorStore(semantic)

    # Set up LanceDB + adapter
    try:
        lancedb = KnowledgeLanceDB(workspace=str(workspace))
        embedding_port = get_default_embedding_port()
        embedding_computer = EmbeddingComputer(embedding_port=embedding_port, max_batch_size=32)
        lancedb_adapter = LanceDBVectorAdapter(lancedb, embedding_computer)
    except (RuntimeError, ValueError) as exc:
        print(f"Error: could not initialize LanceDB: {exc}", file=sys.stderr)
        return 1

    # Create sync engine
    sync = KnowledgeSync(
        jsonl_store=vector_store,
        lancedb_adapter=lancedb_adapter,
        embedding_computer=embedding_computer,
    )

    # Run sync
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            if direction == "jsonl-to-lancedb":
                stats = loop.run_until_complete(sync.sync_to_lancedb())
            elif direction == "lancedb-to-jsonl":
                stats = loop.run_until_complete(sync.sync_from_lancedb(delete_orphan_lancedb=delete_orphan_lancedb))
            else:  # bidirectional
                stats = loop.run_until_complete(sync.sync_bidirectional())
        finally:
            loop.close()
    except (RuntimeError, ValueError) as exc:
        print(f"Error: sync execution failed: {exc}", file=sys.stderr)
        return 1

    # Output
    if output_format == "json":
        print(
            json.dumps(
                {
                    "direction": stats.direction,
                    "jsonl_total": stats.jsonl_total,
                    "lancedb_total": stats.lancedb_total,
                    "items_added_to_lancedb": stats.items_added_to_lancedb,
                    "items_added_to_jsonl": stats.items_added_to_jsonl,
                    "items_removed_from_lancedb": stats.items_removed_from_lancedb,
                    "duration_ms": round(stats.duration_ms, 1),
                    "errors": stats.errors,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"[knowledge-sync] direction={stats.direction}")
        print(f"  JSONL items:   {stats.jsonl_total}")
        print(f"  LanceDB items:  {stats.lancedb_total}")
        print(f"  → LanceDB:     +{stats.items_added_to_lancedb}")
        print(f"  → JSONL:       +{stats.items_added_to_jsonl}")
        print(f"  ← LanceDB:     -{stats.items_removed_from_lancedb} (ghost cleanup)")
        print(f"  duration:       {stats.duration_ms:.1f}ms")
        if stats.errors:
            print(f"  errors:         {len(stats.errors)}")
            for err in stats.errors[:5]:
                print(f"    - {err}")

    return 0


# ---------------------------------------------------------------------------
# CliRouter
# ---------------------------------------------------------------------------


class CliRouter:
    """Single dispatch point for all polaris.delivery.cli subcommands.

    Accepts an ``argparse.Namespace`` (as returned by ``create_parser()``)
    and returns an integer exit code.
    """

    def route(self, args: argparse.Namespace) -> int:
        """Dispatch *args.command* to the appropriate handler."""
        cmd = str(getattr(args, "command", "") or "").strip().lower()

        if cmd == "console":
            return _route_console(args)

        if cmd == "task":
            return _route_task(args)

        if cmd == "session":
            return _route_session(args)

        if cmd == "serve":
            return _route_serve(args)

        if cmd == "cell":
            return _route_cell(args)

        if cmd == "agentic-eval":
            return _route_agentic_eval(args)

        if cmd == "probe":
            return _route_probe(args)

        if cmd == "ingest":
            return _route_ingest(args)

        if cmd == "sync":
            return _route_sync(args)

        # Legacy commands (delegate to polaris_cli logic)
        if cmd in {"chat", "status", "workflow", "test-window"}:
            return self._route_legacy(args)

        # Fallback: unknown command
        print(f"Error: unknown command: {cmd!r}", file=sys.stderr)
        return 1

    def _route_legacy(self, args: argparse.Namespace) -> int:
        """Delegate legacy commands to the existing polaris_cli main()."""
        try:
            from polaris.delivery.cli.polaris_cli import main as legacy_main

            return legacy_main()
        except (RuntimeError, ValueError) as exc:
            logger.warning("legacy route failed: %s", exc)
            print(f"Error in legacy command: {exc}", file=sys.stderr)
            return 1
