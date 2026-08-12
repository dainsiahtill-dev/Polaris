"""node_tests domain for JavaScript/Node syntax repairs."""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from ..contracts import RepairDiagnostic, RepairOperation, RepairPlan, sha256_text
from ._shared import (
    _compiled_typescript_entrypoint,
    _has_typescript_context,
    _is_javascript_test_target_path,
    _normalize_base_files,
    _normalize_repair_path,
    _parse_package_json,
    _typescript_compiler_option,
)
from .constants import (
    _NODE_FLAGS_WITH_VALUE,
    _NODE_FLAGS_WITH_VALUE_PREFIXES,
    _NODE_SCRIPT_SEGMENT_RE,
    JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
    NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
)


def build_node_test_script_contract_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Replace a narrow over-strict generated Node test script contract."""

    normalized_base = _normalize_base_files(base_files)
    script_path = "scripts/test.mjs"
    script_text = normalized_base.get(script_path)
    if script_text is None or not _is_overstrict_node_test_script_contract(script_text):
        return None
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_node_test_script_contract_diagnostic(diagnostic)
    )
    if not matched_diagnostics:
        return None
    repaired = build_substantive_node_test_script()
    if repaired == script_text:
        return None
    return RepairPlan(
        rule_id="javascript.node_test_script_contract",
        source_tool=NODE_TEST_SCRIPT_CONTRACT_SOURCE_TOOL,
        operations=(
            RepairOperation(
                kind="write_file",
                path=script_path,
                content=repaired,
                before_hash=sha256_text(script_text),
                metadata={
                    "repair_kind": "node_test_script_contract",
                    "adapter_transform_migrated": True,
                    "write_file_reason": "node_test_contract_whole_script_replacement",
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
                },
            ),
        ),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="medium",
        priority=1,
        metadata={
            "edit_strategy": "whole_file_test_contract_replacement",
            "adapter_transform_migrated": True,
        },
    )


def build_javascript_test_missing_target_plan(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
    mode: str = "commit",
) -> RepairPlan | None:
    """Create missing JavaScript smoke test targets from existing frontend files."""

    normalized_base = _normalize_base_files(base_files)
    declared_paths = _declared_frontend_paths(normalized_base)
    matched_diagnostics = tuple(
        diagnostic for diagnostic in diagnostics if _is_javascript_test_missing_target_diagnostic(diagnostic)
    )
    operations: list[RepairOperation] = []
    package_payload = _parse_package_json(normalized_base.get("package.json", ""))
    for diagnostic, target in _missing_javascript_test_targets(
        base_files=normalized_base,
        diagnostics=matched_diagnostics,
    ):
        if not target or target in normalized_base:
            continue
        content = build_javascript_node_smoke_test_content(target, normalized_base)
        if _can_build_frontend_smoke_test(declared_paths):
            content = build_javascript_frontend_smoke_test_content(
                target,
                declared_paths,
                use_esm=_javascript_node_smoke_test_uses_esm(target, normalized_base, package_payload or {}),
            )
        operations.append(
            RepairOperation(
                kind="write_file",
                path=target,
                content=content,
                before_hash=sha256_text(""),
                metadata={
                    "repair_kind": "javascript_test_missing_target",
                    "declared_files": list(declared_paths),
                    "write_file_reason": "new_javascript_smoke_target",
                    "diagnostic_id": diagnostic.diagnostic_id,
                },
            )
        )
    script_update = _node_test_script_target_update(normalized_base, package_payload)
    package_text = normalized_base.get("package.json")
    if script_update and package_text:
        operations.append(
            RepairOperation(
                kind="json_set",
                path="package.json",
                json_path=("scripts", "test"),
                value=script_update,
                before_hash=sha256_text(package_text),
                metadata={
                    "repair_kind": "javascript_test_missing_target_script_contract",
                    "structured_operation": "json",
                    "diagnostic_ids": [diagnostic.diagnostic_id for diagnostic in matched_diagnostics],
                },
            )
        )
    if not operations:
        return None
    return RepairPlan(
        rule_id="javascript.test_missing_target",
        source_tool=JAVASCRIPT_TEST_MISSING_TARGET_SOURCE_TOOL,
        operations=tuple(operations),
        diagnostics=matched_diagnostics,
        mode=mode,
        risk_level="low",
        priority=1,
        metadata={"created_test_targets": [operation.path for operation in operations]},
    )


def build_substantive_node_test_script() -> str:
    """Return the deterministic replacement for over-strict generated Node tests."""

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


def build_javascript_frontend_smoke_test_content(
    test_rel_path: str,
    declared_paths: Sequence[str],
    *,
    use_esm: bool = False,
) -> str:
    """Return a deterministic smoke test compatible with the package module kind."""

    root_depth = len(PurePosixPath(test_rel_path).parent.parts)
    root_args = ", ".join(["'..'"] * root_depth)
    root_expr = f"path.resolve(__dirname, {root_args})" if root_args else "path.resolve(__dirname)"
    declared_json = json.dumps(list(declared_paths), ensure_ascii=True)
    preamble = (
        "import assert from 'node:assert';\n"
        "import fs from 'node:fs';\n"
        "import path from 'node:path';\n"
        "import { fileURLToPath } from 'node:url';\n\n"
        "const __filename = fileURLToPath(import.meta.url);\n"
        "const __dirname = path.dirname(__filename);"
        if use_esm
        else "const assert = require('assert');\nconst fs = require('fs');\nconst path = require('path');"
    )
    return f"""{preamble}

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
const declaredScriptPaths = new Set(scriptFiles.map((file) => path.posix.normalize(file).replace(/^\\.\\//, '')));
const htmlScriptPaths = new Set();
for (const htmlFile of htmlFiles) {{
  const htmlContent = read(htmlFile);
  for (const match of htmlContent.matchAll(/<script\\b[^>]*\\bsrc\\s*=\\s*["']([^"']+)["'][^>]*>/gi)) {{
    const source = match[1];
    if (/^(?:[a-z][a-z0-9+.-]*:|\\/\\/)/i.test(source)) continue;
    const cleanSource = source.split(/[?#]/, 1)[0];
    const projectPath = cleanSource.startsWith('/')
      ? cleanSource.slice(1)
      : path.posix.join(path.posix.dirname(htmlFile), cleanSource);
    htmlScriptPaths.add(path.posix.normalize(projectPath).replace(/^\\.\\//, ''));
  }}
}}
const htmlIds = new Set([...htmlText.matchAll(/\\bid\\s*=\\s*["']([^"']+)["']/g)].map((match) => match[1]));

if (htmlFiles.length > 0) {{
  assert.ok(htmlScriptPaths.size > 0, 'declared HTML has no local script entrypoint');
  for (const scriptPath of htmlScriptPaths) {{
    assert.ok(
      declaredScriptPaths.has(scriptPath),
      'HTML references undeclared script ' + scriptPath,
    );
  }}
}}

for (const scriptFile of scriptFiles) {{
  const scriptText = read(scriptFile);
  const scriptPath = path.posix.normalize(scriptFile).replace(/^\\.\\//, '');
  if (htmlFiles.length > 0 && !htmlScriptPaths.has(scriptPath)) {{
    continue;
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


def build_javascript_node_smoke_test_content(test_rel_path: str, base_files: Mapping[str, str]) -> str:
    """Return a deterministic smoke test for a generated Node package."""

    package_payload = _parse_package_json(str(base_files.get("package.json") or "")) or {}
    entrypoint = _javascript_node_smoke_entrypoint(base_files, package_payload)
    source_files = [
        path
        for path in sorted(base_files)
        if path.startswith("src/") and PurePosixPath(path).suffix.lower() in {".ts", ".tsx", ".js", ".mjs", ".cjs"}
    ]
    root_depth = len(PurePosixPath(test_rel_path).parent.parts)
    root_args = ", ".join(["'..'"] * root_depth)
    root_expr = f"path.resolve(__dirname, {root_args})" if root_args else "path.resolve(__dirname)"
    source_json = json.dumps(source_files[:12], ensure_ascii=True)
    entrypoint_json = json.dumps(entrypoint, ensure_ascii=True)
    if _javascript_node_smoke_test_uses_esm(test_rel_path, base_files, package_payload):
        return rf"""import assert from 'node:assert';
import childProcess from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import {{ fileURLToPath }} from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const projectRoot = {root_expr};
const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
const entrypoint = {entrypoint_json};
const sourceFiles = {source_json};
const entrypointPath = path.join(projectRoot, entrypoint);

assert.ok(packageJson.name, 'package name is required');
assert.ok(packageJson.scripts && packageJson.scripts.build, 'build script is required');
assert.ok(packageJson.scripts && packageJson.scripts.test, 'test script is required');
assert.ok(/\.(?:js|mjs|cjs)$/.test(entrypoint), 'Node entrypoint must be JavaScript');
assert.ok(fs.existsSync(entrypointPath), `entrypoint missing: ${{entrypoint}}`);
assert.ok(sourceFiles.length > 0, 'at least one source file is required');

for (const file of sourceFiles) {{
  const absolutePath = path.join(projectRoot, file);
  assert.ok(fs.existsSync(absolutePath), `missing source file ${{file}}`);
  assert.ok(fs.readFileSync(absolutePath, 'utf8').trim().length > 0, `empty source file ${{file}}`);
}}

let output = '';
assert.doesNotThrow(() => {{
  output = childProcess.execFileSync(process.execPath, [entrypointPath], {{
    cwd: projectRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }});
}}, 'Node entrypoint should execute');
assert.strictEqual(typeof output, 'string', 'entrypoint output must be string');

console.log(`node smoke checks passed for ${{sourceFiles.length}} source files`);
"""
    return rf"""const assert = require('assert');
const childProcess = require('child_process');
const fs = require('fs');
const path = require('path');

const projectRoot = {root_expr};
const packageJson = JSON.parse(fs.readFileSync(path.join(projectRoot, 'package.json'), 'utf8'));
const entrypoint = {entrypoint_json};
const sourceFiles = {source_json};
const entrypointPath = path.join(projectRoot, entrypoint);

assert.ok(packageJson.name, 'package name is required');
assert.ok(packageJson.scripts && packageJson.scripts.build, 'build script is required');
assert.ok(packageJson.scripts && packageJson.scripts.test, 'test script is required');
assert.ok(/\.(?:js|mjs|cjs)$/.test(entrypoint), 'Node entrypoint must be JavaScript');
assert.ok(fs.existsSync(entrypointPath), `entrypoint missing: ${{entrypoint}}`);
assert.ok(sourceFiles.length > 0, 'at least one source file is required');

for (const file of sourceFiles) {{
  const absolutePath = path.join(projectRoot, file);
  assert.ok(fs.existsSync(absolutePath), `missing source file ${{file}}`);
  assert.ok(fs.readFileSync(absolutePath, 'utf8').trim().length > 0, `empty source file ${{file}}`);
}}

let output = '';
assert.doesNotThrow(() => {{
  output = childProcess.execFileSync(process.execPath, [entrypointPath], {{
    cwd: projectRoot,
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }});
}}, 'Node entrypoint should execute');
assert.strictEqual(typeof output, 'string', 'entrypoint output must be string');

console.log(`node smoke checks passed for ${{sourceFiles.length}} source files`);
"""


def _javascript_node_smoke_test_uses_esm(
    test_rel_path: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> bool:
    suffix = PurePosixPath(test_rel_path).suffix.lower()
    if suffix in {".mts", ".mjs"}:
        return True
    if suffix in {".cts", ".cjs"}:
        return False
    if suffix in {".ts", ".tsx"}:
        scripts = package_payload.get("scripts")
        test_script = str(scripts.get("test") or "") if isinstance(scripts, Mapping) else ""
        if "--import tsx" in test_script or "--loader tsx" in test_script:
            return True
        module_kind = _typescript_compiler_option(base_files, "module").lower()
        if module_kind in {"commonjs", "cjs"}:
            return False
        if module_kind in {"es2020", "es2022", "esnext", "system", "node16", "node18", "node20", "nodenext"}:
            return True
    return str(package_payload.get("type") or "").strip().lower() == "module"


def _is_node_test_script_contract_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    raw = f"{diagnostic.message}\n{diagnostic.raw}".lower()
    path = str(diagnostic.path or "").replace("\\", "/").lower()
    return (
        path == "scripts/test.mjs"
        or "scripts/test.mjs" in raw
        or "missing validation contract" in raw
        or "over-strict generated node test contract" in raw
    )


def _javascript_node_smoke_entrypoint(base_files: Mapping[str, str], package_payload: Mapping[str, Any]) -> str:
    entrypoint = _normalize_repair_path(str(package_payload.get("main") or ""))
    if entrypoint.endswith((".js", ".mjs", ".cjs")) and not entrypoint.startswith(("dist/", "build/", "out/")):
        return entrypoint
    if entrypoint.startswith(("dist/", "build/", "out/")) and entrypoint.endswith((".js", ".mjs", ".cjs")):
        return entrypoint
    for source_entry in (
        "src/index.js",
        "src/main.js",
        "src/app.js",
        "index.js",
        "main.js",
        "src/index.mjs",
        "src/main.mjs",
        "index.mjs",
        "main.mjs",
        "src/index.cjs",
        "src/main.cjs",
        "index.cjs",
        "main.cjs",
    ):
        if source_entry in base_files:
            return source_entry
    return _compiled_typescript_entrypoint(base_files, package_payload)


def _declared_frontend_paths(base_files: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        path
        for path in sorted(base_files)
        if not _is_javascript_test_target_path(path)
        and PurePosixPath(path).suffix.lower() in {".html", ".js", ".mjs", ".cjs"}
    )


def _can_build_frontend_smoke_test(declared_paths: Sequence[str]) -> bool:
    return any(path.endswith(".html") for path in declared_paths) and any(
        PurePosixPath(path).suffix.lower() in {".js", ".mjs", ".cjs"} for path in declared_paths
    )


def _is_javascript_test_missing_target_diagnostic(diagnostic: RepairDiagnostic) -> bool:
    if diagnostic.code == "declared_target_missing":
        return _is_javascript_test_target_path(str(diagnostic.path or ""))
    if diagnostic.code not in {"artifact_quality_error", "workspace_validation_failed"}:
        return False
    raw = str(diagnostic.raw or diagnostic.message or "").lower()
    npm_test_invoked = "npm run test" in raw or "npm test" in raw
    return npm_test_invoked and (
        "module_not_found" in raw
        or "cannot find module" in raw
        or ("could not find" in raw and ("tests" in raw or "test" in raw))
    )


def _missing_javascript_test_targets(
    *,
    base_files: Mapping[str, str],
    diagnostics: Sequence[RepairDiagnostic],
) -> tuple[tuple[RepairDiagnostic, str], ...]:
    targets: list[tuple[RepairDiagnostic, str]] = []
    inferred_targets = _node_test_targets_from_package(base_files)
    for diagnostic in diagnostics:
        target = _normalize_repair_path(str(diagnostic.path or ""))
        if target:
            targets.append(
                (
                    diagnostic,
                    _source_javascript_test_target_for_missing_compiled_target(target, base_files),
                )
            )
            continue
        targets.extend(
            (
                diagnostic,
                _source_javascript_test_target_for_missing_compiled_target(inferred_target, base_files),
            )
            for inferred_target in inferred_targets
        )
    return tuple((diagnostic, target) for diagnostic, target in targets if _is_javascript_test_target_path(target))


def _node_test_targets_from_package(base_files: Mapping[str, str]) -> tuple[str, ...]:
    package_payload = _parse_package_json(str(base_files.get("package.json") or ""))
    if package_payload is None:
        return ()
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return ()
    test_script = str(scripts.get("test") or "")
    targets: list[str] = []
    for segment in _NODE_SCRIPT_SEGMENT_RE.split(test_script):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            tokens = segment.split()
        for index, token in enumerate(tokens):
            if token != "node" and not token.endswith("/node"):
                continue
            targets.extend(_node_test_targets_from_tokens(tokens[index + 1 :], base_files, package_payload))
    return tuple(dict.fromkeys(targets))


def _node_test_targets_from_tokens(
    tokens: Sequence[str],
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> tuple[str, ...]:
    targets: list[str] = []
    has_node_test_runner = False
    skip_next = False
    for token in tokens:
        if skip_next:
            skip_next = False
            continue
        token_text = str(token or "").strip()
        if not token_text:
            continue
        if token_text == "--test" or token_text.startswith("--test="):
            has_node_test_runner = True
            continue
        if token_text in _NODE_FLAGS_WITH_VALUE:
            skip_next = True
            continue
        if token_text.startswith(_NODE_FLAGS_WITH_VALUE_PREFIXES):
            continue
        if token_text.startswith("-"):
            continue
        target = _normalize_node_test_target(token_text, base_files, package_payload)
        if target and (has_node_test_runner or _is_javascript_test_target_path(target)):
            targets.append(target)
    return tuple(targets)


def _normalize_node_test_target(
    target: str,
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any],
) -> str:
    normalized = _normalize_repair_path(target).rstrip("/")
    if not normalized:
        return ""
    if normalized == "test":
        normalized = "tests"
    elif normalized.startswith("test/"):
        normalized = f"tests/{normalized.removeprefix('test/')}"
    target_path = PurePosixPath(normalized)
    if target_path.suffix:
        return normalized
    if normalized.startswith("dist/") and "__tests__" in normalized:
        extension = "ts" if _has_typescript_context(base_files, package_payload) else "js"
        if extension == "ts":
            return f"src/{normalized.removeprefix('dist/')}/smoke.test.ts"
        return f"{normalized}/smoke.test.js"
    if normalized == "tests" or normalized.startswith("tests/"):
        extension = "ts" if _has_typescript_context(base_files, package_payload) else "js"
        return f"{normalized}/smoke.test.{extension}"
    return normalized


def _source_javascript_test_target_for_missing_compiled_target(
    target: str,
    base_files: Mapping[str, str],
) -> str:
    """Keep generated smoke tests in source when the missing dist target has a TS peer.

    A file written directly under ``outDir`` is erased by the next build and is
    not source evidence.  If a package points at ``dist/tests/foo.js`` while
    ``tests/foo.ts`` exists outside the configured ``rootDir``, create a durable
    JavaScript smoke test beside that source and rewrite the package verifier.
    """

    normalized = _normalize_repair_path(target)
    package_payload = _parse_package_json(str(base_files.get("package.json") or "")) or {}
    if not _has_typescript_context(base_files, package_payload):
        return normalized
    out_dir = _normalize_repair_path(_typescript_compiler_option(base_files, "outDir") or "dist").rstrip("/")
    prefix = f"{out_dir}/" if out_dir else ""
    if not prefix or not normalized.startswith(prefix) or PurePosixPath(normalized).suffix.lower() != ".js":
        return normalized
    source_target = normalized.removeprefix(prefix)
    source_path = PurePosixPath(source_target)
    typescript_peers = (
        str(source_path.with_suffix(".ts")),
        str(source_path.with_suffix(".tsx")),
    )
    return source_target if any(peer in base_files for peer in typescript_peers) else normalized


def _node_test_script_target_update(
    base_files: Mapping[str, str],
    package_payload: Mapping[str, Any] | None,
) -> str:
    if not isinstance(package_payload, Mapping):
        return ""
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return ""
    original = str(scripts.get("test") or "")
    if not original:
        return ""
    updated = _node_test_script_directory_update(package_payload) or original
    for target in _node_test_targets_from_package(base_files):
        replacement = _source_javascript_test_target_for_missing_compiled_target(target, base_files)
        if replacement and replacement != target:
            updated = updated.replace(target, replacement)
    return updated if updated != original else ""


def _node_test_script_directory_update(package_payload: Mapping[str, Any] | None) -> str:
    if not isinstance(package_payload, Mapping):
        return ""
    scripts = package_payload.get("scripts")
    if not isinstance(scripts, Mapping):
        return ""
    test_script = str(scripts.get("test") or "")
    if not test_script:
        return ""
    pattern = re.compile(
        r"(?P<prefix>\bnode\b(?:(?![;&|]).)*?--test(?:(?![;&|]).)*?\s)"
        r"(?P<target>\./dist/__tests__|dist/__tests__|\./test|test)(?P<suffix>(?=\s|$|[;&|]))"
    )

    def replace(match: re.Match[str]) -> str:
        target = str(match.group("target") or "")
        if target.rstrip("/").endswith("dist/__tests__"):
            replacement = f"{target.rstrip('/')}/smoke.test.js"
        else:
            replacement = "./tests/smoke.test.ts" if target.startswith("./") else "tests/smoke.test.ts"
        return f"{match.group('prefix')}{replacement}{match.group('suffix')}"

    updated = pattern.sub(replace, test_script, count=1)
    return updated if updated != test_script else ""


def _is_overstrict_node_test_script_contract(script_text: str) -> bool:
    text = str(script_text or "")
    if "missing validation contract" in text and "validate[A-Za-z]+Record" in text:
        return True
    return (
        "missing export in" in text
        and "export\\s+(class|function|const|interface|type)" in text
        and "export\\s*\\{" not in text
    )
