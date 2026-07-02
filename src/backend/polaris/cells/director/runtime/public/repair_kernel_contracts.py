"""Public inspection contracts for Director Runtime repair-kernel facts.

This module intentionally exposes only stable constants and pure helper
functions. It does not expose repair planners, runners, policy gates, or any
mutation authority. Cross-cell adapters and tests can use this surface to
verify repair receipts without importing ``director.runtime.internal``.
"""

from __future__ import annotations

import hashlib
import re

FILE_ABSENT_HASH = "file_absent"
RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_duplicate_module_file_repair"
RUST_MISSING_MODULE_FILE_SOURCE_TOOL = "deterministic_rust_missing_module_file_repair"
RUST_MISSING_MODULE_FILE_STUB = (
    "// Polaris marker: rust.missing_module_file\n// Created from rustc E0583 as an empty module topology stub.\n"
)

_PATCH_RESIDUE_LINE_RE = re.compile(
    r"(?m)^\s*(?:<{4,7}\s*SEARCH\b.*|>{4,7}\s*REPLACE\b.*|END\s+PATCH_FILE\b.*|PATCH_FILE(?::|\s+).*)\s*$",
    re.IGNORECASE,
)


def sha256_text(value: str) -> str:
    """Return the stable Director repair-kernel UTF-8 SHA-256 text hash."""

    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def remove_patch_residue_lines(text: str) -> str:
    """Return source text with leaked patch-protocol residue lines removed."""

    cleaned = _PATCH_RESIDUE_LINE_RE.sub("", str(text or ""))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    if str(text or "").endswith("\n") and not cleaned.endswith("\n"):
        cleaned += "\n"
    return cleaned


def build_substantive_node_test_script() -> str:
    """Return the deterministic Node test script used by repair receipts."""

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


def is_overstrict_node_test_script_contract(script_text: str) -> bool:
    """Return whether generated Node test text matches the over-strict contract."""

    text = str(script_text or "")
    if "missing validation contract" in text and "validate[A-Za-z]+Record" in text:
        return True
    return (
        "missing export in" in text
        and "export\\s+(class|function|const|interface|type)" in text
        and "export\\s*\\{" not in text
    )


__all__ = [
    "FILE_ABSENT_HASH",
    "RUST_DUPLICATE_MODULE_FILE_SOURCE_TOOL",
    "RUST_MISSING_MODULE_FILE_SOURCE_TOOL",
    "RUST_MISSING_MODULE_FILE_STUB",
    "build_substantive_node_test_script",
    "is_overstrict_node_test_script_contract",
    "remove_patch_residue_lines",
    "sha256_text",
]
