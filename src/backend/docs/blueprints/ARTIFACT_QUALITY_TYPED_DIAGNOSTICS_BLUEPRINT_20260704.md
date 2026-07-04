# Artifact Quality Typed Diagnostics Blueprint (2026-07-04)

## 1. Problem

Artifact quality scanning already exposes `ArtifactQualityIssue`, but most
scanner findings still enter `ArtifactQualityEvidence` through
`errors: tuple[str, ...]` and are then reparsed into typed issues. That keeps
legacy string consumers working, but it makes each new diagnostic format a
potential parser drift point.

## 2. Target

`ArtifactQualityEvidence` remains string-compatible for legacy callers, while
new scanner code should prefer direct typed facts:

```
scanner/parser fact
  -> ArtifactQualityIssue(code, message, path, source, metadata)
  -> ArtifactQualityEvidence.issues
  -> string errors only as compatibility projection
```

Typed issues are evidence, not repair authorization. Repair still flows through
`Diagnostic -> Plan -> Policy/Execute -> Receipt -> Revalidate`.

## 3. 2026-07-04 Increment

- `_artifact_quality_evidence()` now accepts `issues=...` in addition to
  legacy `errors=...`.
- Direct issues are deduplicated against string-projected issues so callers do
  not receive one typed fact from the scanner and a second typed fact reparsed
  from the same legacy string.
- Workspace path scanner failures now emit direct typed issues:
  `workspace_path_unresolved` and `workspace_path_missing`.
- Interface-ledger validation now has `DeclaredInterfaceValidationIssue` and
  `validate_declared_interface_issues_against_snapshot()`. The legacy
  `validate_declared_interfaces_against_snapshot()` remains as a string
  projection, while artifact-quality evidence consumes the typed payload with
  stable codes `declared_interface_missing` and
  `declared_interface_signature_missing`.
- `_scan_file_evidence()` now returns per-file legacy strings and direct
  `ArtifactQualityIssue` values together. The legacy `_scan_file()` string
  API remains as a projection, while `scan_workspace_artifact_quality_evidence()`
  consumes direct per-file issues and avoids reparsing the same error into a
  duplicate typed issue.
- `_scan_package_manifest_evidence()` now owns package-manifest direct typed
  issues with `source="package_manifest_scanner"`. The legacy
  `_scan_package_manifest()` API remains a string projection, but npm manifest
  findings no longer need to be reparsed from prose to become
  `ArtifactQualityIssue(code="npm_manifest_invalid", path="package.json")`.
- Source syntax failures from `check_source_file_syntax()` now emit direct
  `ArtifactQualityIssue(code="syntax_error", source="source_syntax_checker")`
  at file-scan time, while preserving the legacy error string for existing
  callers.
- `_scan_typescript_import_evidence()` now owns TypeScript/JavaScript import
  direct typed issues with `source="typescript_import_scanner"` for unresolved
  relative imports, undeclared runtime imports, and missing `@types/node`
  obligations. The legacy `_scan_typescript_imports()` API remains a string
  projection.
- `_scan_typescript_syntax_red_flag_evidence()` now owns direct typed issues
  for TypeScript syntax red flags with
  `source="typescript_syntax_red_flag_scanner"`, while the legacy
  `_scan_typescript_syntax_red_flags()` API remains a string projection.
- `_scan_html_typescript_module_script_evidence()` now owns direct typed
  issues for HTML module scripts that point at TypeScript source files with
  `source="html_module_script_scanner"`, while the legacy
  `_scan_html_typescript_module_scripts()` API remains a string projection.
- `_scan_package_module_type_mismatch_evidence()` now owns direct typed issues
  for `package.json` declaring `type=module` while workspace JavaScript uses
  CommonJS runtime syntax, with `source="package_module_type_scanner"`. The
  legacy `_scan_package_module_type_mismatch()` API remains a string
  projection.
- Workspace artifact evidence no longer calls the legacy `_scan_python_imports()`
  file scanner. Python cross-file symbol drift remains owned by the typed
  `cross_artifact_consistency` scanner and its repair-plan projection, avoiding
  a second Python import fact source for the same unresolved symbol.

## 4. Verification

- `rtk pytest src/backend/polaris/tests/unit/kernelone/quality/test_interface_ledger.py src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py -q`
- `rtk ruff check src/backend/polaris/kernelone/quality/interface_ledger.py src/backend/polaris/kernelone/quality/artifact_quality.py src/backend/polaris/tests/unit/kernelone/quality/test_interface_ledger.py src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py`
- `rtk mypy src/backend/polaris/kernelone/quality/interface_ledger.py src/backend/polaris/kernelone/quality/artifact_quality.py`
- `rtk pytest src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py -q`
- `rtk ruff check src/backend/polaris/kernelone/quality/artifact_quality.py src/backend/polaris/kernelone/quality/tests/test_artifact_quality.py`
- `rtk mypy src/backend/polaris/kernelone/quality/artifact_quality.py`
