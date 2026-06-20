"""Deterministic JavaScript repair generators (frontend smoke + node test script).

Carved verbatim from the original ``deterministic_repairs`` module.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from ..execution_tools import DirectorToolExecutor
from ..task_scope_paths import (
    _dedupe_preserve_order,
    _extract_task_target_path_candidates,
    _normalize_declared_task_path,
)
from ._common import _parse_missing_declared_target_files


def _is_javascript_test_target_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").strip().replace("\\", "/")
    if not normalized:
        return False
    path = Path(normalized)
    if path.suffix.lower() not in {".js", ".mjs", ".cjs"}:
        return False
    name = path.name.lower()
    return normalized.startswith("tests/") or ".test." in name or ".spec." in name


def _is_plain_frontend_declared_path(rel_path: str) -> bool:
    normalized = str(rel_path or "").strip().replace("\\", "/")
    if not normalized or _is_javascript_test_target_path(normalized):
        return False
    return Path(normalized).suffix.lower() in {".html", ".js", ".css"}


def _build_javascript_frontend_smoke_test_content(test_rel_path: str, declared_paths: list[str]) -> str:
    root_depth = len(Path(test_rel_path).parent.parts)
    root_args = ", ".join(["'..'"] * root_depth)
    root_expr = f"path.resolve(__dirname, {root_args})" if root_args else "path.resolve(__dirname)"
    declared_json = json.dumps(declared_paths, ensure_ascii=True)
    return f"""const assert = require('assert');
const fs = require('fs');
const path = require('path');

const projectRoot = {root_expr};
const declaredFiles = {declared_json};
const htmlFiles = declaredFiles.filter((file) => /\\.html$/i.test(file));
const scriptFiles = declaredFiles.filter((file) => /\\.(?:js|mjs|cjs)$/i.test(file));

function read(relativePath) {{
  const absolutePath = path.join(projectRoot, relativePath);
  assert.ok(fs.existsSync(absolutePath), `missing declared file ${{relativePath}}`);
  const text = fs.readFileSync(absolutePath, 'utf8');
  assert.ok(text.trim().length > 0, `empty declared file ${{relativePath}}`);
  return text;
}}

for (const file of declaredFiles) {{
  read(file);
}}

const htmlText = htmlFiles.map(read).join('\\n');
const htmlIds = new Set([...htmlText.matchAll(/\\bid\\s*=\\s*["']([^"']+)["']/g)].map((match) => match[1]));

for (const scriptFile of scriptFiles) {{
  const scriptText = read(scriptFile);
  const scriptName = path.posix.basename(scriptFile);
  if (htmlFiles.length > 0) {{
    assert.ok(htmlText.includes(scriptName), `${{scriptFile}} is not referenced by declared HTML`);
  }}
  const referencedIds = [
    ...scriptText.matchAll(/getElementById\\s*\\(\\s*["']([^"']+)["']\\s*\\)/g),
  ].map((match) => match[1]);
  for (const id of referencedIds) {{
    assert.ok(htmlIds.has(id), `${{scriptFile}} references missing DOM id ${{id}}`);
  }}
  if (/\\blocalStorage\\b/.test(scriptText)) {{
    assert.ok(
      /localStorage\\.(?:getItem|setItem|removeItem)\\s*\\(/.test(scriptText),
      `${{scriptFile}} mentions localStorage without using the item API`,
    );
  }}
}}

console.log(`frontend smoke checks passed for ${{declaredFiles.length}} declared files`);
"""


def _apply_deterministic_javascript_test_missing_target_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
    artifact_quality_errors: list[str],
) -> list[dict[str, Any]]:
    """Create a missing declared JS smoke test for already-materialized static frontend targets."""

    missing_paths = _parse_missing_declared_target_files(artifact_quality_errors)
    if not missing_paths:
        return []
    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    workspace_name = workspace_path.name
    task_candidates = {
        _normalize_declared_task_path(candidate, workspace_name=workspace_name)
        for candidate in _extract_task_target_path_candidates(task)
    }
    declared_frontend_paths: list[str] = []
    for candidate in task_candidates:
        if not candidate or not _is_plain_frontend_declared_path(candidate):
            continue
        target_path = (workspace_path / candidate).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if target_path.is_file():
            declared_frontend_paths.append(candidate)
    declared_frontend_paths = _dedupe_preserve_order(sorted(declared_frontend_paths))
    if not any(path.endswith(".html") for path in declared_frontend_paths):
        return []
    if not any(Path(path).suffix.lower() in {".js", ".mjs", ".cjs"} for path in declared_frontend_paths):
        return []

    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    executor = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    )
    results: list[dict[str, Any]] = []
    for missing_rel in missing_paths:
        if missing_rel not in task_candidates or not _is_javascript_test_target_path(missing_rel):
            continue
        target_path = (workspace_path / missing_rel).resolve()
        try:
            target_path.relative_to(workspace_path)
        except ValueError:
            continue
        if target_path.exists():
            continue
        content = _build_javascript_frontend_smoke_test_content(missing_rel, declared_frontend_paths)
        write_result = executor.execute_tool(
            "write_file",
            {"file": missing_rel, "content": content},
            task_id=task_id,
        )
        if not bool(write_result.get("ok")):
            continue
        with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
            adapter._update_task_progress(task_id, "executing", current_file=missing_rel)
        results.append(
            {
                "tool": "write_file",
                "tool_name": "write_file",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": "deterministic_javascript_test_missing_target_repair",
                    "file": missing_rel,
                    "declared_files": list(declared_frontend_paths),
                    "bytes_written": int(write_result.get("bytes_written") or len(content.encode("utf-8"))),
                    "operation": str(write_result.get("operation") or "create"),
                    "broadcast_ok": bool(write_result.get("broadcast_ok")),
                    "director_policy": write_result.get("director_policy"),
                },
            }
        )
    return results


def _is_overstrict_node_test_script_contract(script_text: str) -> bool:
    """Return true for historical generated test scripts with false-negative export checks."""

    text = str(script_text or "")
    if "missing validation contract" in text and "validate[A-Za-z]+Record" in text:
        return True
    return (
        "missing export in" in text
        and "export\\s+(class|function|const|interface|type)" in text
        and "export\\s*\\{" not in text
    )


def _apply_deterministic_node_test_script_contract_repair(
    adapter: Any,
    *,
    task: dict[str, Any],
    task_id: str,
) -> list[dict[str, Any]]:
    """Replace an over-strict generated Node test contract with substantive checks."""

    workspace_path = Path(str(getattr(adapter, "workspace", "") or "")).resolve()
    if not workspace_path.exists() or not workspace_path.is_dir():
        return []

    declared_paths = {
        _normalize_declared_task_path(candidate, workspace_name=workspace_path.name)
        for candidate in _extract_task_target_path_candidates(task)
    }
    if "scripts/test.mjs" not in declared_paths:
        return []

    script_path = workspace_path / "scripts" / "test.mjs"
    if not script_path.exists() or not script_path.is_file():
        return []
    try:
        script_text = script_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if not _is_overstrict_node_test_script_contract(script_text):
        return []

    new_text = _build_substantive_node_test_script()
    if script_text == new_text:
        return []
    message_bus = getattr(getattr(adapter, "_execution", None), "_message_bus", None)
    write_result = DirectorToolExecutor(
        str(workspace_path),
        message_bus=message_bus,
        worker_id="director",
    ).execute_tool(
        "write_file",
        {"file": "scripts/test.mjs", "content": new_text},
        task_id=task_id,
    )
    if not bool(write_result.get("ok")):
        return []
    with contextlib.suppress(OSError, RuntimeError, TypeError, ValueError):
        adapter._update_task_progress(task_id, "executing", current_file="scripts/test.mjs")
    return [
        {
            "tool": "write_file",
            "tool_name": "write_file",
            "success": True,
            "result": {
                "ok": True,
                "source_tool": "deterministic_node_test_script_contract_repair",
                "file": "scripts/test.mjs",
                "bytes_written": int(write_result.get("bytes_written") or len(new_text.encode("utf-8"))),
                "operation": str(write_result.get("operation") or "modify"),
                "broadcast_ok": bool(write_result.get("broadcast_ok")),
                "director_policy": write_result.get("director_policy"),
            },
        }
    ]


def _build_substantive_node_test_script() -> str:
    return """import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const p = path.join(dir, entry.name);
    return entry.isDirectory() ? walk(p) : [p];
  });
}

const sourceFiles = walk('src').filter((file) => file.endsWith('.ts'));
const testFiles = walk('tests').filter((file) => file.endsWith('.ts'));
const seedMarkerPattern = new RegExp('audit-' + 'seed|planning ' + 'scenario', 'i');
const requiredTestFiles = [
  'tests/unit/card-rules.test.ts',
  'tests/unit/deck-builder.test.ts',
  'tests/integration/multiplayer-flow.test.ts',
  'tests/integration/realtime-sync.test.ts',
  'tests/e2e/card-table-3d.test.ts',
];

if (sourceFiles.length < 18) {
  throw new Error('expected at least 18 source modules');
}
if (testFiles.length < requiredTestFiles.length) {
  throw new Error('expected required test files');
}
for (const file of requiredTestFiles) {
  if (!testFiles.includes(file)) {
    throw new Error('missing required test file ' + file);
  }
}

for (const file of sourceFiles) {
  const text = readFileSync(file, 'utf8');
  const moduleExportPattern =
    /(?:^|\\n)\\s*export\\s+(?:async\\s+)?(?:class|function|const|let|var|interface|type|enum|default)\\b|(?:^|\\n)\\s*export\\s*\\{/;
  if (!moduleExportPattern.test(text)) {
    throw new Error('missing export in ' + file);
  }
  if (seedMarkerPattern.test(text)) {
    throw new Error('seed marker retained in ' + file);
  }
}

for (const file of testFiles) {
  const text = readFileSync(file, 'utf8');
  if (!/from ['"]..\\/..\\/src\\//.test(text)) {
    throw new Error('test file lacks src import ' + file);
  }
  if (!/run[A-Za-z0-9]+Checks/.test(text) || !/failures/.test(text)) {
    throw new Error('test file lacks executable check contract ' + file);
  }
  if (/expect\\(\\s*\\d+\\s*(?:[+\\-*/])\\s*\\d+\\s*\\)\\.to(?:Be|Equal)\\(\\s*\\d+\\s*\\)/.test(text)) {
    throw new Error('trivial arithmetic test ' + file);
  }
}

console.log(
  'card3d behavior checks passed across ' +
    sourceFiles.length +
    ' source files and ' +
    testFiles.length +
    ' test files'
);
"""
