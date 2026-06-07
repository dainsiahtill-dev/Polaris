"""Execute a read plan via a ReadToolPort and collect findings (UTF-8)."""
from __future__ import annotations

from typing import Any

from polaris.cells.roles.scout.public.contracts import ScoutFinding
from polaris.cells.roles.scout.internal.ports import ReadToolPort


def retrieve(
    port: ReadToolPort,
    plan: list[tuple[str, list[str]]],
) -> tuple[list[ScoutFinding], dict[str, Any]]:
    """Run each (tool, args); collect findings + coverage. Never raises per-call."""
    findings: list[ScoutFinding] = []
    tools_used: list[str] = []
    errors: list[str] = []
    truncated = False

    for tool, args in plan:
        if tool not in tools_used:
            tools_used.append(tool)
        try:
            result = port.run(tool, args)
        except (RuntimeError, ValueError, OSError) as exc:
            errors.append(f"{tool}({args}): {exc}")
            continue
        if not result.get("ok", False):
            errors.append(f"{tool}: {result.get('error') or 'not ok'}")
            continue
        if result.get("truncated"):
            truncated = True
        for hit in result.get("hits", []) or []:
            findings.append(
                ScoutFinding(
                    path=str(hit.get("file") or ""),
                    line=_as_int(hit.get("line")),
                    snippet=str(hit.get("text") or "").strip(),
                )
            )

    coverage = {
        "tools_used": tools_used,
        "errors": errors,
        "truncated": truncated,
        "raw_findings": len(findings),
    }
    return findings, coverage


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
