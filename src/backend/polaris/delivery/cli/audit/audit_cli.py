#!/usr/bin/env python3
"""Audit CLI - machine-friendly audit command interface.

Supported commands:
- triage
- hops
- diagnose
- scan
- check-region
- trace
- verify-chain
- tail
- export
- corruption
- stats

CRITICAL: 所有文本文件 I/O 必须使用 UTF-8 编码。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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

    from polaris.cells.audit.diagnosis.public import run_audit_command
    from polaris.kernelone.fs.encoding import enforce_utf8

    return run_audit_command, enforce_utf8


logger = logging.getLogger(__name__)

# Exit codes
EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_VERIFY_FAILED = 2
EXIT_NOT_FOUND = 3
EXIT_API_ERROR = 4


def _output_format(args: argparse.Namespace) -> str:
    if getattr(args, "human", False):
        return "human"
    return str(getattr(args, "format", "json") or "json").strip().lower()


def _extract_option(argv: list[str], name: str) -> str | None:
    prefix = f"{name}="
    for idx, token in enumerate(argv):
        if token == name and idx + 1 < len(argv):
            return argv[idx + 1]
        if token.startswith(prefix):
            return token[len(prefix) :]
    return None


def _extract_flag(argv: list[str], name: str) -> bool:
    return any(token == name for token in argv)


def _write_text_utf8(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def _to_json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2, ensure_ascii=False)


def _print_human(result: dict[str, Any]) -> None:
    command = str(result.get("command") or "")
    status = str(result.get("status") or "")
    mode = str(result.get("mode") or "")
    logger.info(f"=== Audit {command} ===")
    logger.info(f"Status: {status}")
    logger.info(f"Mode: {mode}")
    logger.info(f"Generated: {result.get('generated_at')}")

    errors = result.get("errors")
    if isinstance(errors, list) and errors:
        logger.info("\nErrors:")
        for item in errors:
            if isinstance(item, dict):
                code = item.get("code")
                msg = item.get("message")
                logger.info(f"  - {code}: {msg}")
            else:
                logger.info(f"  - {item}")

    data = result.get("data")
    if not isinstance(data, dict):
        if data is not None:
            logger.info("\nData:")
            logger.info(_to_json_text(data))
        return

    if command == "triage":
        logger.info(f"\nRun ID: {data.get('run_id')}")
        logger.info(f"Task ID: {data.get('task_id')}")
        logger.info(f"Trace ID: {data.get('trace_id')}")
        pm_history = data.get("pm_quality_history", [])
        tool_audit = data.get("director_tool_audit", {})
        logger.info(f"PM events: {len(pm_history) if isinstance(pm_history, list) else 0}")
        logger.info(f"Tool calls: {tool_audit.get('total', 0) if isinstance(tool_audit, dict) else 0}")
        logger.info(f"Tool failures: {tool_audit.get('failed', 0) if isinstance(tool_audit, dict) else 0}")
        if data.get("failure_hops"):
            hops = data["failure_hops"]
            if isinstance(hops, dict):
                logger.info(f"Failure code: {hops.get('failure_code')}")
                logger.info(f"Has failure: {hops.get('has_failure')}")
        return

    if command == "hops":
        logger.info(f"\nRun ID: {data.get('run_id')}")
        logger.info(f"Has failure: {data.get('has_failure')}")
        logger.info(f"Ready: {data.get('ready')}")
        logger.info(f"Failure code: {data.get('failure_code')}")
        hop1 = data.get("hop1_phase")
        if isinstance(hop1, dict):
            logger.info(f"Phase: {hop1.get('phase')} ({hop1.get('actor')})")
        return

    if command == "diagnose":
        logger.info(f"\nRun ID: {data.get('run_id')}")
        logger.info(f"Task ID: {data.get('task_id')}")
        logger.info(f"Failure detected: {data.get('failure_detected')}")
        logger.info(f"Recommended action: {data.get('recommended_action')}")
        root = data.get("root_cause")
        if isinstance(root, dict):
            logger.info(f"Root cause: {root.get('category')} (confidence={root.get('confidence')})")
            logger.info(f"Fix suggestion: {root.get('fix_suggestion')}")
        return

    if command == "scan":
        summary = data.get("summary", {})
        logger.info(f"\nScope: {data.get('scope')}")
        logger.info(f"Focus: {data.get('focus')}")
        logger.info(f"Score: {summary.get('score')}")
        logger.info(f"Files scanned: {summary.get('files_scanned')}")
        logger.info(f"Findings: {summary.get('findings_total')}")
        return

    if command == "check-region":
        logger.info(f"\nFile: {data.get('file')}")
        logger.info(f"Function: {data.get('function_name')}")
        line_range = data.get("line_range")
        if isinstance(line_range, dict):
            logger.info(f"Line range: {line_range.get('start')}-{line_range.get('end')}")
        summary = data.get("summary")
        if isinstance(summary, dict):
            logger.info(f"Findings: {summary.get('findings_total')}")
            logger.info(f"Score: {summary.get('score')}")
        return

    if command == "trace":
        logger.info(f"\nTrace ID: {data.get('trace_id')}")
        logger.info(f"Events: {data.get('event_count')}")
        logger.info(f"First timestamp: {data.get('first_timestamp')}")
        logger.info(f"Last timestamp: {data.get('last_timestamp')}")
        return

    if command == "verify-chain":
        logger.info(f"\nChain valid: {data.get('chain_valid')}")
        logger.info(f"Total events: {data.get('total_events')}")
        logger.info(f"Has events: {data.get('has_events')}")
        logger.info(f"Empty chain: {data.get('empty_chain')}")
        logger.info(f"Gap count: {data.get('gap_count')}")
        return

    if command == "tail":
        events = data.get("events", [])
        logger.info(f"\nEvents: {len(events) if isinstance(events, list) else 0}")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                ts = str(event.get("timestamp") or "")[11:19]
                event_type = event.get("event_type", "unknown")
                source = event.get("source", {})
                role = source.get("role", "unknown") if isinstance(source, dict) else "unknown"
                action = event.get("action", {})
                action_name = action.get("name", "") if isinstance(action, dict) else ""
                result_value = action.get("result", "") if isinstance(action, dict) else ""
                suffix = f" [{result_value}]" if result_value else ""
                logger.info(f"  {ts} [{role}] {event_type}: {action_name}{suffix}")
        return

    if command == "corruption":
        records = data.get("records", [])
        logger.info(f"\nRecords: {len(records) if isinstance(records, list) else 0}")
        return

    if command == "stats":
        stats = data.get("stats", {})
        logger.info("\nStatistics:")
        if isinstance(stats, dict):
            for key, value in stats.items():
                logger.info(f"  {key}: {value}")
        return

    if command == "export":
        logger.info(f"\nFormat: {data.get('format')}")
        content = data.get("content")
        if isinstance(content, dict):
            logger.info(f"Record count: {content.get('export_metadata', {}).get('record_count')}")
        return

    if command == "role-info":
        logger.info("\nAudit Role Binding:")
        logger.info(f"  Tech role: {data.get('tech_role_id')}")
        logger.info(f"  Court role: {data.get('court_role_name')} ({data.get('court_role_id')})")
        logger.info(f"  Department: {data.get('court_department')}")
        logger.info(f"  Provider ID: {data.get('provider_id')}")
        logger.info(f"  Provider type: {data.get('provider_type')}")
        logger.info(f"  Model: {data.get('model')}")
        logger.info(f"  Local Ollama preferred: {data.get('prefer_local_ollama')}")
        logger.info(f"  Remote fallback allowed: {data.get('allow_remote_fallback')}")
        return

    logger.info("\nData:")
    logger.info(_to_json_text(data))


def _print_output(result: dict[str, Any], *, output_format: str, output_path: str | None) -> None:
    data = result.get("data")

    if output_path:
        if isinstance(data, dict) and str(result.get("command")) == "export":
            content = data.get("content")
            _write_text_utf8(output_path, _to_json_text(content))
        else:
            _write_text_utf8(output_path, _to_json_text(result))
        logger.info(f"Exported to {output_path}")
        return

    if output_format == "human":
        _print_human(result)
        return

    logger.info(json.dumps(result, indent=2, ensure_ascii=False))


def _exit_code(result: dict[str, Any]) -> int:
    status = str(result.get("status") or "").strip().lower()
    command = str(result.get("command") or "").strip().lower()

    if status == "not_found":
        return EXIT_NOT_FOUND
    if status == "error":
        errors = result.get("errors")
        if isinstance(errors, list) and errors:
            first = errors[0]
            if isinstance(first, dict) and first.get("code") == "api_error":
                return EXIT_API_ERROR
        return EXIT_ERROR
    if command == "verify-chain":
        data = result.get("data")
        if isinstance(data, dict) and data.get("chain_valid") is False:
            return EXIT_VERIFY_FAILED
    return EXIT_SUCCESS


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--runtime-root", help="Runtime root directory")
    parser.add_argument(
        "--mode",
        choices=["auto", "online", "offline"],
        default="auto",
        help="Execution mode (default: auto)",
    )
    parser.add_argument(
        "--format",
        choices=["json", "human"],
        default="json",
        help="Output format (default: json)",
    )
    parser.add_argument("--workspace", help="Workspace path override")
    parser.add_argument("--base-url", help="Backend base URL override")
    parser.add_argument("--human", action="store_true", help=argparse.SUPPRESS)
    return parser


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser()
    parser = argparse.ArgumentParser(
        description="Polaris Audit CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
        conflict_handler="resolve",
        epilog="""
Environment Variables:
  KERNELONE_BACKEND_PORT   - Backend API port (default: 49977)
  KERNELONE_RUNTIME_BASE   - Runtime root directory
  KERNELONE_WORKSPACE      - Workspace path

Exit Codes:
  0 - Success
  1 - General error
  2 - Chain verification failed
  3 - Not found
  4 - API error
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    triage_parser = subparsers.add_parser(
        "triage",
        help="Generate triage bundle",
        parents=[common],
        conflict_handler="resolve",
    )
    triage_parser.add_argument("--run-id", help="Run ID")
    triage_parser.add_argument("--task-id", help="Task ID")
    triage_parser.add_argument("--trace-id", help="Trace ID")

    hops_parser = subparsers.add_parser(
        "hops",
        help="Get 3-hops failure localization",
        parents=[common],
        conflict_handler="resolve",
    )
    hops_parser.add_argument("run_id", help="Run ID")

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Analyze failure chain with 3-hop diagnosis",
        parents=[common],
        conflict_handler="resolve",
    )
    diagnose_parser.add_argument("--run-id", help="Run ID")
    diagnose_parser.add_argument("--task-id", help="Task ID")
    diagnose_parser.add_argument("--error-message", help="Error hint text")
    diagnose_parser.add_argument("--time-range", default="1h", help="Evidence window (e.g. 30m, 2h, 1d)")
    diagnose_parser.add_argument("--depth", type=int, default=3, help="Diagnosis depth (1-3)")

    scan_parser = subparsers.add_parser(
        "scan",
        help="Run project QA scan",
        parents=[common],
        conflict_handler="resolve",
    )
    scan_parser.add_argument(
        "--scope",
        choices=["full", "changed", "region"],
        default="full",
        help="Scan scope",
    )
    scan_parser.add_argument("--focus", help="Focus file/path for region scope")
    scan_parser.add_argument("--max-files", type=int, default=800, help="Maximum files to scan")
    scan_parser.add_argument("--max-findings", type=int, default=300, help="Maximum findings to return")

    check_region_parser = subparsers.add_parser(
        "check-region",
        help="Run focused region audit",
        parents=[common],
        conflict_handler="resolve",
    )
    check_region_parser.add_argument("--file-path", help="Target file path")
    check_region_parser.add_argument("--function-name", help="Target function name")
    check_region_parser.add_argument("--lines", help="Line range, e.g. 10-50")

    trace_parser = subparsers.add_parser(
        "trace",
        help="Query trace timeline",
        parents=[common],
        conflict_handler="resolve",
    )
    trace_parser.add_argument("trace_id", help="Trace ID")
    trace_parser.add_argument("--limit", type=int, default=300, help="Maximum events")

    verify_parser = subparsers.add_parser(
        "verify-chain",
        help="Verify audit chain integrity",
        parents=[common],
        conflict_handler="resolve",
    )
    verify_parser.add_argument(
        "--strict-non-empty",
        action="store_true",
        help="Fail when total_events is 0",
    )

    subparsers.add_parser(
        "role-info",
        help="Show independent audit role/provider binding",
        parents=[common],
        conflict_handler="resolve",
    )

    tail_parser = subparsers.add_parser(
        "tail",
        help="View audit log tail",
        parents=[common],
        conflict_handler="resolve",
    )
    tail_parser.add_argument("-n", "--limit", type=int, default=50, help="Number of events")
    tail_parser.add_argument("--failure-only", action="store_true", help="Show only task_failed events")
    tail_parser.add_argument("--event-type", help="Filter by event type")

    export_parser = subparsers.add_parser(
        "export",
        help="Export audit data",
        parents=[common],
        conflict_handler="resolve",
    )
    export_parser.add_argument(
        "--export-format",
        choices=["json", "csv"],
        default="json",
        help="Export payload format (default: json)",
    )
    export_parser.add_argument("--start-time", help="Start time (ISO8601)")
    export_parser.add_argument("--end-time", help="End time (ISO8601)")
    export_parser.add_argument("--event-types", help="Event types (comma-separated)")
    include_group = export_parser.add_mutually_exclusive_group()
    include_group.add_argument("--include-data", dest="include_data", action="store_true")
    include_group.add_argument("--no-include-data", dest="include_data", action="store_false")
    export_parser.set_defaults(include_data=True)
    export_parser.add_argument("--output", "-o", help="Output file path")

    corruption_parser = subparsers.add_parser(
        "corruption",
        help="View corruption log",
        parents=[common],
        conflict_handler="resolve",
    )
    corruption_parser.add_argument("-n", "--limit", type=int, default=100, help="Max records")

    stats_parser = subparsers.add_parser(
        "stats",
        help="View audit statistics",
        parents=[common],
        conflict_handler="resolve",
    )
    stats_parser.add_argument("--start-time", help="Start time (ISO8601)")
    stats_parser.add_argument("--end-time", help="End time (ISO8601)")

    return parser


def _command_params(args: argparse.Namespace) -> dict[str, Any]:
    command = str(args.command or "")
    if command == "triage":
        return {
            "run_id": args.run_id,
            "task_id": args.task_id,
            "trace_id": args.trace_id,
        }
    if command == "hops":
        return {"run_id": args.run_id}
    if command == "diagnose":
        return {
            "run_id": args.run_id,
            "task_id": args.task_id,
            "error_message": args.error_message,
            "time_range": args.time_range,
            "depth": args.depth,
        }
    if command == "scan":
        return {
            "scope": args.scope,
            "focus": args.focus,
            "max_files": args.max_files,
            "max_findings": args.max_findings,
        }
    if command == "check-region":
        return {
            "file_path": args.file_path,
            "function_name": args.function_name,
            "lines": args.lines,
        }
    if command == "trace":
        return {
            "trace_id": args.trace_id,
            "limit": args.limit,
        }
    if command == "verify-chain":
        return {
            "strict_non_empty": bool(getattr(args, "strict_non_empty", False)),
        }
    if command == "tail":
        return {
            "limit": args.limit,
            "failure_only": bool(args.failure_only),
            "event_type": args.event_type,
        }
    if command == "export":
        return {
            "format": args.export_format,
            "start_time": args.start_time,
            "end_time": args.end_time,
            "event_types": args.event_types,
            "include_data": bool(args.include_data),
        }
    if command == "corruption":
        return {"limit": args.limit}
    if command == "stats":
        return {
            "start_time": args.start_time,
            "end_time": args.end_time,
        }
    return {}


def _collect_audit_role_info(workspace: str | None) -> dict[str, Any]:
    from polaris.bootstrap.config import get_settings
    from polaris.cells.audit.evidence.public import build_audit_llm_binding_config, get_audit_role_descriptor
    from polaris.cells.llm.provider_runtime.public import normalize_provider_type
    from polaris.kernelone.llm.config_store import load_llm_config
    from polaris.kernelone.llm.runtime_config import get_role_model
    from polaris.kernelone.storage.io_paths import build_cache_root

    settings = get_settings()
    workspace_value = str(workspace or settings.workspace or ".")
    binding = build_audit_llm_binding_config(settings)
    role_id = str(binding.role_id or "qa").strip().lower() or "qa"

    provider_id, model = get_role_model(role_id)
    provider_type = ""
    try:
        cache_root = build_cache_root(getattr(settings, "ramdisk_root", "") or "", workspace_value)
        llm_payload = load_llm_config(workspace_value, cache_root, settings=settings)
        providers_raw = llm_payload.get("providers") if isinstance(llm_payload, dict) else {}
        providers: dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}
        provider_cfg_raw = providers.get(provider_id)
        provider_cfg: dict[str, Any] = provider_cfg_raw if isinstance(provider_cfg_raw, dict) else {}
        provider_type = normalize_provider_type(str(provider_cfg.get("type") or "").strip().lower())
    except (RuntimeError, ValueError):
        provider_type = ""

    if not provider_type:
        token = str(provider_id or "").strip().lower()
        if "ollama" in token:
            provider_type = "ollama"
        elif "codex" in token:
            provider_type = "codex_cli"

    descriptor = get_audit_role_descriptor()
    return {
        **descriptor,
        "workspace": workspace_value,
        "provider_id": provider_id,
        "provider_type": provider_type,
        "model": model,
        "audit_llm_enabled": bool(binding.enabled),
        "audit_llm_timeout": int(binding.timeout_seconds),
        "prefer_local_ollama": bool(binding.prefer_local_ollama),
        "allow_remote_fallback": bool(binding.allow_remote_fallback),
    }


def main() -> int:
    run_audit_command, enforce_utf8 = _bootstrap_backend_import_path()
    enforce_utf8()
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout, force=True)
    raw_argv = sys.argv[1:]
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return EXIT_ERROR

    from polaris.bootstrap.assembly import ensure_minimal_kernelone_bindings

    ensure_minimal_kernelone_bindings()

    runtime_root = args.runtime_root or _extract_option(raw_argv, "--runtime-root")
    workspace = args.workspace or _extract_option(raw_argv, "--workspace")
    base_url = args.base_url or _extract_option(raw_argv, "--base-url")

    mode = args.mode
    raw_mode = _extract_option(raw_argv, "--mode")
    if raw_mode in {"auto", "online", "offline"}:
        mode = raw_mode

    if not args.human and _extract_flag(raw_argv, "--human"):
        args.human = True

    command = str(args.command)
    params = _command_params(args)

    effective_workspace = workspace or os.environ.get("KERNELONE_WORKSPACE") or "."
    if command == "role-info":
        result = {
            "command": "role-info",
            "status": "ok",
            "mode": "local",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data": _collect_audit_role_info(effective_workspace),
            "errors": [],
        }
    else:
        result = run_audit_command(
            command,
            params=params,
            mode=mode,
            runtime_root=runtime_root,
            workspace=effective_workspace,
            base_url=base_url,
        )

    if command == "export":
        export_data = result.get("data")
        if isinstance(export_data, dict) and "format" not in export_data:
            export_data["format"] = "json"

    _print_output(
        result,
        output_format=_output_format(args),
        output_path=getattr(args, "output", None),
    )
    return _exit_code(result)


if __name__ == "__main__":
    sys.exit(main())
