"""Tests for the Director Runtime Repair Kernel contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.director.runtime.internal.repair_kernel import (
    PatchComposer,
    RepairAdvisorNote,
    RepairArchetype,
    RepairConvergenceScheduler,
    RepairDiagnostic,
    RepairOperation,
    RepairPlan,
    RepairReceipt,
    RepairRevalidationEvidence,
    RepairRuleDefinition,
    RepairRuleRegistry,
    RepairVerifierSnapshot,
    build_repair_coverage_report,
    build_typescript_hyphenated_identifier_plan,
    build_typescript_object_literal_comma_plan,
    default_repair_rule_registry,
    javascript_syntax as js_syntax,
    normalize_artifact_quality_errors,
    order_repair_plans,
    plan_typescript_object_literal_comma_repair,
    runtime_dispatch as runtime_dispatch_module,
    typescript_syntax as ts_syntax,
)
from polaris.cells.director.runtime.public import (
    DirectorRepairMaterializationAllowedPathsResultV1,
    DirectorRepairMaterializationPlanProbeResultV1,
    DirectorRepairPlanningResultV1,
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairMaterializationAllowedPathsV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    QueryDirectorRepairPlanProbeV1,
    RepairDiagnosticV1,
    RepairReceiptV1,
    normalize_director_repair_issue_diagnostics,
    plan_director_repair,
    query_director_repair_materialization_allowed_paths,
    query_director_repair_materialization_plan_probe,
    query_director_repair_plan_probe,
    service as runtime_public_service,
)
from polaris.cells.director.runtime.public.service import (
    _execution as runtime_public_execution,
    normalize_director_repair_diagnostics,
)
from polaris.cells.director.runtime.tests._repair_kernel_contract_support import (
    _javascript_esm_commonjs_after,
    _javascript_missing_export_after,
    _plan_javascript_missing_export,
)
from polaris.kernelone.quality import artifact_quality_issues_from_errors


def test_public_normalizes_typed_artifact_quality_issues_to_repair_diagnostics() -> None:
    diagnostics = normalize_director_repair_issue_diagnostics(
        (
            {
                "source": "artifact_quality",
                "code": "npm_manifest_invalid",
                "message": "npm package manifest script 'test' is invalid",
                "path": "package.json",
                "severity": "error",
                "line": 12,
                "column": 4,
                "symbol": "scripts.test",
                "metadata": {"script": "test", "column": 99},
            },
        )
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].source == "artifact_quality"
    assert diagnostics[0].code == "npm_manifest_invalid"
    assert diagnostics[0].message == "npm package manifest script 'test' is invalid"
    assert diagnostics[0].path == "package.json"
    assert diagnostics[0].metadata["script"] == "test"
    assert diagnostics[0].metadata["line"] == 12
    assert diagnostics[0].metadata["column"] == 99
    assert diagnostics[0].metadata["symbol"] == "scripts.test"
    assert diagnostics[0].metadata["path"] == "package.json"
    assert diagnostics[0].metadata["source"] == "artifact_quality"
    assert diagnostics[0].metadata["severity"] == "error"


def test_public_repair_diagnostics_preserve_kernelone_issue_locations() -> None:
    issues = artifact_quality_issues_from_errors(
        ("src/main.ts(3,14): error TS2322: Type 'string' is not assignable to type 'number'.",)
    )

    diagnostics = normalize_director_repair_issue_diagnostics(issues)

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "typescript_ts2322"
    assert diagnostics[0].path == "src/main.ts"
    assert diagnostics[0].metadata["line"] == 3
    assert diagnostics[0].metadata["column"] == 14
    assert diagnostics[0].metadata["diagnostic_code"] == "TS2322"
    assert diagnostics[0].metadata["raw"] == (
        "src/main.ts(3,14): error TS2322: Type 'string' is not assignable to type 'number'."
    )


def test_public_repair_diagnostics_preserve_typed_artifact_issue_fields() -> None:
    diagnostics = normalize_director_repair_issue_diagnostics(
        (
            {
                "code": "typescript_ts2307",
                "message": "Cannot find module './missing.js'",
                "path": "src/main.ts",
                "source": "typescript_compiler",
                "severity": "error",
                "line": 2,
                "column": 8,
                "metadata": {"raw": "typed diagnostic", "diagnostic_code": "TS2307"},
            },
        )
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "typescript_ts2307"
    assert diagnostics[0].path == "src/main.ts"
    assert diagnostics[0].source == "typescript_compiler"
    assert diagnostics[0].severity == "error"
    assert diagnostics[0].metadata["path"] == "src/main.ts"
    assert diagnostics[0].metadata["source"] == "typescript_compiler"
    assert diagnostics[0].metadata["severity"] == "error"
    assert diagnostics[0].metadata["diagnostic_code"] == "TS2307"


def test_public_repair_diagnostics_preserve_kernelone_rust_issue_locations() -> None:
    raw_error = "error[E0583]: file not found for module `weather`\n  --> src/main.rs:2:1\n   |\n2  | mod weather;\n"
    issues = artifact_quality_issues_from_errors((raw_error,))

    diagnostics = normalize_director_repair_issue_diagnostics(issues)

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "rust_e0583"
    assert diagnostics[0].path == "src/main.rs"
    assert diagnostics[0].metadata["line"] == 2
    assert diagnostics[0].metadata["column"] == 1
    assert diagnostics[0].metadata["diagnostic_code"] == "E0583"
    assert diagnostics[0].metadata["raw"] == raw_error.strip()


def test_public_repair_diagnostics_preserve_kernelone_go_compile_issue() -> None:
    raw_error = "engine/main.go:10:5: undefined: Weather"
    issues = artifact_quality_issues_from_errors((raw_error,))

    diagnostics = normalize_director_repair_issue_diagnostics(issues)

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "go_compile_error"
    assert diagnostics[0].path == "engine/main.go"
    assert diagnostics[0].metadata["line"] == 10
    assert diagnostics[0].metadata["column"] == 5
    assert diagnostics[0].metadata["language"] == "go"
    assert diagnostics[0].metadata["raw"] == raw_error


def test_public_repair_diagnostics_preserve_import_issue_metadata() -> None:
    issues = artifact_quality_issues_from_errors(
        (
            "Artifact quality scan failed: unresolved import symbol 'WeatherKind' "
            "from 'src.models.weather' in src/engine/forecast.py",
        )
    )

    diagnostics = normalize_director_repair_issue_diagnostics(issues)

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "unresolved_import_symbol"
    assert diagnostics[0].path == "src/engine/forecast.py"
    assert diagnostics[0].metadata["symbol"] == "WeatherKind"
    assert diagnostics[0].metadata["module"] == "src.models.weather"
    assert diagnostics[0].metadata["importer_path"] == "src/engine/forecast.py"


def test_public_repair_diagnostics_accept_top_level_import_issue_fields() -> None:
    diagnostics = normalize_director_repair_issue_diagnostics(
        (
            {
                "source": "artifact_quality",
                "code": "unresolved_relative_import",
                "message": "unresolved relative import './engine/runner' in src/index.ts",
                "metadata": {"raw": "raw diagnostic"},
                "specifier": "./engine/runner",
                "importer_path": "src/index.ts",
            },
        )
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].path == "src/index.ts"
    assert diagnostics[0].metadata["raw"] == "raw diagnostic"
    assert diagnostics[0].metadata["specifier"] == "./engine/runner"
    assert diagnostics[0].metadata["importer_path"] == "src/index.ts"
    assert diagnostics[0].metadata["source"] == "artifact_quality"


def test_public_repair_diagnostics_accept_top_level_owner_path() -> None:
    diagnostics = normalize_director_repair_issue_diagnostics(
        (
            {
                "source": "cross_artifact_consistency",
                "code": "cross_artifact_unresolved_import_symbol",
                "message": "WeatherKind is imported but not exported",
                "specifier": "src.models.weather",
                "importer_path": "src/engine/forecast.py",
                "owner_path": "src/models/weather.py",
                "symbol": "WeatherKind",
                "details": {
                    "available_exports": ["WeatherReport"],
                    "resolution_path": "src.models.weather",
                },
            },
        )
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].path == "src/engine/forecast.py"
    assert diagnostics[0].metadata["importer_path"] == "src/engine/forecast.py"
    assert diagnostics[0].metadata["owner_path"] == "src/models/weather.py"
    assert diagnostics[0].metadata["symbol"] == "WeatherKind"
    assert diagnostics[0].metadata["details"] == {
        "available_exports": ["WeatherReport"],
        "resolution_path": "src.models.weather",
    }


def test_public_repair_planning_projects_typed_diagnostics() -> None:
    diagnostic = RepairDiagnosticV1(
        source="artifact_quality",
        code="typescript_ts9999",
        message="Unknown future compiler error.",
        path="src/app.ts",
        metadata={"line": 3, "column": 14, "confidence": "parser"},
    )

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="unsupported.future_rule",
            diagnostics=(diagnostic,),
        )
    )
    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["diagnostic_count"] == 1
    assert payload["diagnostics"][0]["code"] == "typescript_ts9999"
    assert payload["diagnostics"][0]["path"] == "src/app.ts"
    assert payload["diagnostics"][0]["metadata"]["line"] == 3
    assert payload["diagnostics"][0]["metadata"]["confidence"] == "parser"


def test_public_repair_planning_accepts_typed_artifact_quality_issues() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="unsupported.future_rule",
            artifact_quality_issues=(
                {
                    "source": "artifact_quality",
                    "code": "typescript_ts9999",
                    "message": "Unknown future compiler error.",
                    "path": "src/app.ts",
                    "metadata": {"line": 3, "column": 14, "confidence": "parser"},
                },
            ),
        )
    )
    payload = result.to_dict()

    assert payload["ok"] is False
    assert payload["diagnostic_count"] == 1
    assert payload["diagnostics"][0]["code"] == "typescript_ts9999"
    assert payload["diagnostics"][0]["path"] == "src/app.ts"
    assert payload["diagnostics"][0]["metadata"]["column"] == 14
    assert payload["diagnostics"][0]["metadata"]["confidence"] == "parser"


def test_public_repair_planning_preserves_typed_planner_route(monkeypatch: pytest.MonkeyPatch) -> None:
    source_tool = "test.public_typed_planner_route"
    captured: dict[str, object] = {}

    def legacy_planner(
        base_files: dict[str, str],
        artifact_quality_errors: tuple[str, ...],
        advisor_notes: tuple[RepairAdvisorNote, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairPlanning:
        del base_files, artifact_quality_errors, advisor_notes, mode
        raise AssertionError("public service should not force typed diagnostics through the legacy planner")

    def typed_planner(
        base_files: dict[str, str],
        diagnostics: tuple[RepairDiagnostic, ...],
        artifact_quality_errors: tuple[str, ...],
        advisor_notes: tuple[RepairAdvisorNote, ...] | None,
        mode: str,
    ) -> runtime_dispatch_module.RuntimeRepairPlanning:
        del base_files, advisor_notes
        captured["diagnostics"] = diagnostics
        captured["artifact_quality_errors"] = artifact_quality_errors
        captured["mode"] = mode
        return runtime_dispatch_module.RuntimeRepairPlanning(
            source_tool=source_tool,
            diagnostics=diagnostics,
            plan=None,
            composition=None,
            error_code="typed_planner_seen",
        )

    def runner(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("runner should not be called by planning")

    bindings = dict(runtime_dispatch_module._RUNTIME_REPAIR_BINDINGS)
    bindings[source_tool] = runtime_dispatch_module.RuntimeRepairBinding(
        source_tool=source_tool,
        language="test",
        rule_id="test.public_typed_planner_route",
        planner=legacy_planner,  # type: ignore[arg-type]
        runner=runner,  # type: ignore[arg-type]
        typed_planner=typed_planner,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(runtime_dispatch_module, "_RUNTIME_REPAIR_BINDINGS", bindings)

    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool,
            artifact_quality_errors=("legacy string should not override typed diagnostics",),
            diagnostics=(
                RepairDiagnosticV1(
                    source="artifact_quality",
                    code="typescript_ts9999",
                    message="Unknown future compiler error.",
                    path="src/app.ts",
                    metadata={"raw": "src/app.ts(1,1): error TS9999: Unknown future compiler error."},
                ),
            ),
        )
    )

    assert result.error_code == "typed_planner_seen"
    assert captured["mode"] == "commit"
    assert captured["artifact_quality_errors"] == ("src/app.ts(1,1): error TS9999: Unknown future compiler error.",)
    diagnostics = captured["diagnostics"]
    assert isinstance(diagnostics, tuple)
    assert diagnostics[0].code == "typescript_ts9999"
    assert diagnostics[0].path == "src/app.ts"


def test_normalizer_builds_typed_typescript_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(["src/app.ts(3,14): error TS2304: Cannot find name 'Widget'."])

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_ts2304"
    assert diagnostic.path == "src/app.ts"
    assert diagnostic.line == 3
    assert diagnostic.column == 14


def test_normalizer_collapses_tap_failure_island_into_one_actionable_diagnostic() -> None:
    raw = """
TAP version 13
# Subtest: 正常路径：extractDreamKeywords 提取梦境关键词
not ok 2 - 正常路径：extractDreamKeywords 提取梦境关键词
  ---
  duration_ms: 1.7
  type: 'test'
  location: '/workspace/tests/product.test.js:46:1'
  failureType: 'testCodeFailure'
  error: |-
    The expression evaluated to a falsy value:

      assert.ok(keywords.includes('火焰'))

  code: 'ERR_ASSERTION'
  name: 'AssertionError'
  expected: true
  actual: false
  operator: '=='
  stack: |-
    TestContext.<anonymous> (file:///workspace/tests/product.test.js:57:10)
    Test.runInAsyncScope (node:async_hooks:214:14)
  ...
ok 3 - 边界路径：空内容返回空关键词
1..23
# tests 23
# pass 22
# fail 1
"""

    diagnostics = normalize_artifact_quality_errors([raw])

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "verifier"
    assert diagnostic.code == "verifier_test_failure"
    assert diagnostic.path == "/workspace/tests/product.test.js"
    assert diagnostic.line == 46
    assert diagnostic.column == 1
    assert diagnostic.metadata["framework"] == "tap"
    assert diagnostic.metadata["test_name"] == "正常路径：extractDreamKeywords 提取梦境关键词"
    assert diagnostic.metadata["expected"] == "true"
    assert diagnostic.metadata["actual"] == "false"
    assert "火焰" in diagnostic.raw
    assert "# pass 22" not in diagnostic.raw


def test_normalizer_does_not_invent_failure_for_passing_tap_output() -> None:
    diagnostics = normalize_artifact_quality_errors(
        ["TAP version 13\nok 1 - creates item\n1..1\n# tests 1\n# pass 1\n# fail 0"]
    )

    assert all(diagnostic.code != "verifier_test_failure" for diagnostic in diagnostics)


def test_normalizer_bounds_many_tap_failures_without_losing_audit_cardinality() -> None:
    raw = "TAP version 13\n" + "".join(
        f"not ok {index} - failure {index}\n  location: '/workspace/tests/product.test.js:{index}:1'\n"
        f"  expected: true\n  actual: false\n"
        for index in range(1, 101)
    )

    diagnostics = normalize_artifact_quality_errors([raw])

    assert len(diagnostics) == 12
    assert all(item.code == "verifier_test_failure" for item in diagnostics)
    assert all(item.metadata["total_failure_count"] == 100 for item in diagnostics)
    assert all(item.metadata["truncated_failure_count"] == 88 for item in diagnostics)
    assert all(len(str(item.metadata["source_blob_sha256"])) == 64 for item in diagnostics)


def test_normalizer_preserves_structured_artifact_quality_issue() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "typescript_ts1005",
                "message": "',' expected.",
                "path": "src/app.ts",
                "line": 3,
                "column": 14,
                "raw": "src/app.ts(3,14): error TS1005: ',' expected.",
                "metadata": {
                    "confidence": "parser",
                    "diagnostic_archetype": "object_literal_syntax",
                },
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_ts1005"
    assert diagnostic.message == "',' expected."
    assert diagnostic.path == "src/app.ts"
    assert diagnostic.line == 3
    assert diagnostic.column == 14
    assert diagnostic.raw == "src/app.ts(3,14): error TS1005: ',' expected."
    assert diagnostic.metadata["confidence"] == "parser"
    assert diagnostic.metadata["diagnostic_archetype"] == "object_literal_syntax"


def test_normalizer_preserves_structured_npm_script_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "npm_manifest_invalid",
                "message": "npm manifest script contract violation",
                "path": "package.json",
                "manifest_path": "package.json",
                "script_name": "lint",
                "script_issue": "placeholder_command",
                "raw": "typed script issue",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "npm_manifest_invalid"
    assert diagnostic.path == "package.json"
    assert diagnostic.metadata["manifest_path"] == "package.json"
    assert diagnostic.metadata["script_name"] == "lint"
    assert diagnostic.metadata["script_issue"] == "placeholder_command"


def test_normalizer_preserves_structured_typescript_suggestion_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "typescript_ts2820",
                "message": "typed string literal suggestion",
                "path": "src/models/Market.ts",
                "line": 3,
                "column": 5,
                "actual": '"pre_open"',
                "suggestion": '"pre-open"',
                "raw": "typed metadata only",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "typescript_ts2820"
    assert diagnostic.path == "src/models/Market.ts"
    assert diagnostic.metadata["actual"] == '"pre_open"'
    assert diagnostic.metadata["suggestion"] == '"pre-open"'


def test_normalizer_preserves_structured_typescript_issue_kind_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "artifact_quality",
                "code": "typescript_syntax_red_flag",
                "message": "typed syntax red flag",
                "path": "src/middleware/auth.ts",
                "issue_kind": "escaped_newline",
                "raw_path": "/tmp/factory-bench/src/middleware/auth.ts",
                "raw": "typed issue metadata only",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "typescript_syntax_red_flag"
    assert diagnostic.path == "src/middleware/auth.ts"
    assert diagnostic.metadata["issue_kind"] == "escaped_newline"
    assert diagnostic.metadata["raw_path"] == "/tmp/factory-bench/src/middleware/auth.ts"


def test_normalizer_preserves_structured_javascript_runtime_global_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "runtime_smoke",
                "code": "javascript_dom_global_in_node_runtime",
                "message": "Browser DOM global window is not available in Node.",
                "path": "dist/web.js",
                "runtime_global": "window",
                "raw": "typed runtime smoke metadata only",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "javascript_dom_global_in_node_runtime"
    assert diagnostic.path == "dist/web.js"
    assert diagnostic.metadata["runtime_global"] == "window"


def test_public_normalizer_preserves_structured_diagnostic_payload() -> None:
    diagnostics = normalize_director_repair_diagnostics(
        [
            {
                "source": "artifact_quality",
                "code": "missing_entrypoint_target",
                "message": "package.json script points to missing src/index.js",
                "target_file": "src/index.js",
                "metadata": {
                    "confidence": "parser",
                    "diagnostic_archetype": "manifest_entrypoint_contract",
                },
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "missing_entrypoint_target"
    assert diagnostic.message == "package.json script points to missing src/index.js"
    assert diagnostic.path == "src/index.js"
    assert diagnostic.metadata["confidence"] == "parser"
    assert diagnostic.metadata["diagnostic_archetype"] == "manifest_entrypoint_contract"


def test_normalizer_builds_typed_typescript_return_object_semicolon_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: TypeScript return object contains "
            "semicolon-terminated property in src/models/task.ts"
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.source == "artifact_quality"
    assert diagnostic.code == "typescript_return_object_property_semicolon"
    assert diagnostic.path == "src/models/task.ts"
    coverage = default_repair_rule_registry().coverage(diagnostics)

    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].known_rule_matched is True
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.object_literal_property_semicolon"


def test_normalizer_builds_cross_artifact_unresolved_symbol_diagnostic() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: unresolved import symbol 'WeatherReport' "
            "from './weather' in src/forecast.ts (sibling module does not define it)"
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "cross_artifact_unresolved_import_symbol"
    assert diagnostic.path == "src/forecast.ts"
    assert diagnostic.metadata == {
        "symbol": "WeatherReport",
        "module": "./weather",
        "contract_plane": "cross_artifact_interface",
    }

    coverage = default_repair_rule_registry().coverage(diagnostics)
    assert coverage.covered_diagnostic_count == 1
    assert coverage.executable_runtime_plan_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "typescript.unresolved_import_symbol_missing_export"


def test_normalizer_preserves_flat_typescript_symbol_coherence_fields() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "typescript_symbol_coherence_scanner",
                "code": "typescript_import_unresolved_symbol",
                "message": "Missing export WeatherReport",
                "path": "src/forecast.ts",
                "importer_path": "src/forecast.ts",
                "exporter_path": "src/weather.ts",
                "specifier": "./weather",
                "imported_symbol": "WeatherReport",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "typescript_import_unresolved_symbol"
    assert diagnostic.path == "src/forecast.ts"
    assert diagnostic.metadata["importer_path"] == "src/forecast.ts"
    assert diagnostic.metadata["exporter_path"] == "src/weather.ts"
    assert diagnostic.metadata["specifier"] == "./weather"
    assert diagnostic.metadata["imported_symbol"] == "WeatherReport"


def test_normalizer_preserves_flat_npm_script_artifact_fields() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "npm_script_entrypoint_scanner",
                "code": "npm_script_missing_local_entrypoint",
                "message": "start script references a missing entrypoint",
                "path": "package.json",
                "manifest_path": "package.json",
                "script_name": "start",
                "script_issue": "missing_local_entrypoint",
                "entrypoint": "src/index.js",
                "config_path": "tsconfig.json",
                "target_directory": "tests",
            }
        ]
    )

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.metadata["manifest_path"] == "package.json"
    assert diagnostic.metadata["script_name"] == "start"
    assert diagnostic.metadata["script_issue"] == "missing_local_entrypoint"
    assert diagnostic.metadata["entrypoint"] == "src/index.js"
    assert diagnostic.metadata["config_path"] == "tsconfig.json"
    assert diagnostic.metadata["target_directory"] == "tests"


def test_normalizer_preserves_flat_scanner_artifact_fields() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            {
                "source": "typescript_project_typecheck",
                "code": "typescript_project_typecheck_failed",
                "message": "project typecheck failed",
                "artifact_path": "package.json",
                "collision_name": "Task",
                "command": "tsc --noEmit --pretty false",
                "declared_type": "module",
                "detail": "src/app.ts(1,1): error TS1005",
                "exit_code": 2,
                "export_name": "Task",
                "html_path": "index.html",
                "package_root": ".",
                "required_dependency": "typescript",
                "runtime_syntax": "esm",
                "script_src": "./src/main.ts",
                "source_path": "src/main.ts",
                "syntax_error": "TS1005",
            }
        ]
    )

    assert len(diagnostics) == 1
    metadata = diagnostics[0].metadata
    assert metadata["artifact_path"] == "package.json"
    assert metadata["collision_name"] == "Task"
    assert metadata["command"] == "tsc --noEmit --pretty false"
    assert metadata["declared_type"] == "module"
    assert metadata["detail"] == "src/app.ts(1,1): error TS1005"
    assert metadata["exit_code"] == 2
    assert metadata["export_name"] == "Task"
    assert metadata["html_path"] == "index.html"
    assert metadata["package_root"] == "."
    assert metadata["required_dependency"] == "typescript"
    assert metadata["runtime_syntax"] == "esm"
    assert metadata["script_src"] == "./src/main.ts"
    assert metadata["source_path"] == "src/main.ts"
    assert metadata["syntax_error"] == "TS1005"


def test_normalizer_preserves_diagnostic_kind_from_top_level_when_metadata_absent() -> None:
    """Regression: scanner emits diagnostic_kind at top level with no metadata dict.

    The normalizer must copy diagnostic_kind into RepairDiagnostic.metadata
    rather than silently dropping it at the scanner->repair boundary.
    """
    payload = {
        "source": "go_compile_scanner",
        "code": "go_compile_error",
        "message": "undefined: Identifier",
        "path": "cmd/main.go",
        "line": 42,
        "column": 5,
        "raw": "cmd/main.go:42:5: undefined: Identifier",
        "diagnostic_kind": "undefined_identifier",
    }
    diagnostics = normalize_artifact_quality_errors([payload])

    assert len(diagnostics) == 1
    diag = diagnostics[0]
    # Core fields must not degrade.
    assert diag.source == "go_compile_scanner"
    assert diag.code == "go_compile_error"
    assert diag.message == "undefined: Identifier"
    assert diag.path == "cmd/main.go"
    assert diag.line == 42
    assert diag.column == 5
    # diagnostic_kind must survive even when the input has no metadata dict.
    assert diag.metadata["diagnostic_kind"] == "undefined_identifier"


def test_normalizer_preserves_diagnostic_kind_from_top_level_when_metadata_present() -> None:
    """Regression: scanner emits diagnostic_kind at top level alongside a
    metadata dict that does NOT contain diagnostic_kind.

    The normalizer must still hoist diagnostic_kind into RepairDiagnostic.metadata
    so downstream repair rules can key on it.
    """
    payload = {
        "source": "go_compile_scanner",
        "code": "go_compile_error",
        "message": "undefined: Identifier",
        "path": "cmd/main.go",
        "line": 42,
        "column": 5,
        "raw": "cmd/main.go:42:5: undefined: Identifier",
        "diagnostic_kind": "undefined_identifier",
        "metadata": {
            "language": "go",
            "identifier": "Identifier",
        },
    }
    diagnostics = normalize_artifact_quality_errors([payload])

    assert len(diagnostics) == 1
    diag = diagnostics[0]
    # Core fields must not degrade.
    assert diag.source == "go_compile_scanner"
    assert diag.code == "go_compile_error"
    assert diag.message == "undefined: Identifier"
    assert diag.path == "cmd/main.go"
    assert diag.line == 42
    assert diag.column == 5
    # Existing metadata keys must survive.
    assert diag.metadata["language"] == "go"
    assert diag.metadata["identifier"] == "Identifier"
    # diagnostic_kind must be hoisted when metadata does not contain it.
    assert diag.metadata["diagnostic_kind"] == "undefined_identifier"


def test_cross_artifact_unresolved_symbol_routes_to_python_rule_for_python_paths() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "Artifact quality scan failed: unresolved import symbol 'WeatherReport' "
            "from 'src.models.weather' in src/engine/forecast.py (sibling module does not define it)"
        ]
    )

    coverage = default_repair_rule_registry().coverage(diagnostics)

    assert coverage.covered_diagnostic_count == 1
    assert coverage.items[0].matched_rules[0].rule_id == "python.unresolved_import_symbol"


def test_repair_rule_registry_reports_known_and_unknown_diagnostic_coverage() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "TypeScript syntax check failed: src/models/Flight.ts(6,5): error TS1005: ',' expected.",
            "src/app.ts(3,14): error TS9999: Unknown future compiler error.",
        ]
    )

    report = default_repair_rule_registry().coverage(diagnostics)
    payload = report.to_dict()

    assert payload["total_diagnostics"] == 2
    assert payload["covered_diagnostic_count"] == 1
    assert payload["uncovered_diagnostic_count"] == 1
    assert payload["coverage_gap_count"] == 1
    assert payload["rule_discovery_required"] is True
    assert payload["coverage_gap_languages"] == ["typescript"]
    assert payload["executable_runtime_plan_diagnostic_count"] == 1
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["known_rule_matched"] is True
    assert payload["items"][0]["executable_runtime_plan_matched"] is True
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["matched_rule_ids"] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]
    assert payload["items"][0]["runtime_plan_rule_ids"] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]
    assert payload["items"][0]["archetypes"] == ["invalid_identifier", "object_literal_syntax"]
    assert payload["items"][0]["phases"] == ["quality_repair"]
    assert payload["items"][1]["known_rule_matched"] is False
    assert payload["items"][1]["matched_rule_ids"] == []
    assert payload["items"][1]["diagnostic_archetype"] == "object_literal_syntax"
    assert payload["items"][1]["diagnostic_phase"] == "quality_repair"
    assert payload["items"][1]["diagnostic_language"] == "typescript"
    assert payload["coverage_gaps"][0]["known_rule_matched"] is False
    assert payload["coverage_gaps"][0]["audit_reason"] == "known_rule_matched=false"
    assert payload["coverage_gaps"][0]["missing_capability"] == "deterministic_repair_rule"


def test_repair_coverage_uses_typed_metadata_archetype_before_message_guessing() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="custom_quality_gate",
        message="opaque quality issue without dependency import syntax terms",
        path="src/widget.custom",
        metadata={
            "language": "typescript",
            "diagnostic_archetype": "missing_dependency",
        },
    )

    payload = build_repair_coverage_report((diagnostic,)).to_dict()

    assert payload["items"][0]["known_rule_matched"] is False
    assert payload["items"][0]["diagnostic_archetype"] == "missing_dependency"
    assert payload["items"][0]["archetype_suggestion"] == "missing_dependency"
    assert payload["items"][0]["diagnostic_phase"] == "dependency_resolution"


def test_repair_coverage_uses_typed_code_for_phase_before_message_guessing() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_custom_quality",
        message="opaque structured diagnostic",
        path="src/widget.ts",
    )

    payload = build_repair_coverage_report((diagnostic,)).to_dict()

    assert payload["items"][0]["known_rule_matched"] is False
    assert payload["items"][0]["diagnostic_archetype"] == "unknown"
    assert payload["items"][0]["diagnostic_language"] == "typescript"
    assert payload["items"][0]["diagnostic_phase"] == "quality_repair"


def test_repair_coverage_uses_typed_source_path_for_phase_before_message_guessing() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="custom_quality_gate",
        message="opaque structured diagnostic",
        path="src/widget.go",
    )

    payload = build_repair_coverage_report((diagnostic,)).to_dict()

    assert payload["items"][0]["known_rule_matched"] is False
    assert payload["items"][0]["diagnostic_archetype"] == "unknown"
    assert payload["items"][0]["diagnostic_language"] == "go"
    assert payload["items"][0]["diagnostic_phase"] == "quality_repair"


def test_repair_rule_registry_matches_language_specific_go_and_rust_rules() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "main.go:8:2: import path must be string",
            "error[E0433]: failed to resolve: use of unresolved module or unlinked crate `tokio`\n"
            "  --> src/main.rs:3:5",
            "error[E0277]: the trait bound `Widget: Copy` is not satisfied\n  --> src/lib.rs:12:10",
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()

    assert payload["covered_diagnostic_count"] == 3
    assert payload["executable_runtime_plan_diagnostic_count"] == 3
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["items"][0]["matched_rule_ids"] == ["go.bare_import_string"]
    assert payload["items"][0]["runtime_plan_rule_ids"] == ["go.bare_import_string"]
    assert payload["items"][0]["metadata_only_match"] is False
    assert payload["items"][0]["diagnostic_language"] == "go"
    assert payload["items"][1]["matched_rule_ids"] == ["rust.unlinked_crate_dependency"]
    assert payload["items"][1]["runtime_plan_rule_ids"] == ["rust.unlinked_crate_dependency"]
    assert payload["items"][1]["metadata_only_match"] is False
    assert payload["items"][1]["diagnostic_phase"] == "dependency_resolution"
    assert payload["items"][2]["matched_rule_ids"] == ["rust.missing_trait_derive"]
    assert payload["items"][2]["runtime_plan_rule_ids"] == ["rust.missing_trait_derive"]
    assert payload["items"][2]["diagnostic_archetype"] == "incompatible_derive"


def test_repair_rule_registry_matches_existing_multilanguage_legacy_strategy_metadata() -> None:
    diagnostics = normalize_artifact_quality_errors(
        [
            "src/main.cpp:3:10: fatal error: 'engine.hpp' file not found",
            "src/Main.java:7: error: cannot find symbol",
            'Traceback (most recent call last):\n  File "tests/test_app.py", line 2, in <module>\n'
            "ModuleNotFoundError: No module named 'app'",
            "Error: Cannot find module './src/index.js'",
            "SyntaxError: The requested module './app.js' does not provide an export named 'run'",
            "TypeScript project typecheck failed: src/app.ts(1,10): error TS2305: "
            "Module '\"./model\"' has no exported member 'Widget'.",
            "src/spec.test.ts(1,1): error TS2582: Cannot find name 'describe'.",
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()
    matched_source_tools = [item["matched_source_tools"] for item in payload["items"]]

    assert payload["covered_diagnostic_count"] == 7
    assert payload["metadata_only_diagnostic_count"] == 0
    assert payload["executable_runtime_plan_diagnostic_count"] == 7
    assert matched_source_tools[0] == ["deterministic_cpp_include_path_repair"]
    assert payload["items"][0]["runtime_plan_rule_ids"] == ["cpp.include_path"]
    assert payload["items"][0]["diagnostic_language"] == "cpp"
    assert "deterministic_java_post_repair" in matched_source_tools[1]
    assert payload["items"][1]["diagnostic_language"] == "java"
    assert matched_source_tools[2] == ["deterministic_python_missing_module_alias_repair"]
    assert payload["items"][2]["diagnostic_language"] == "python"
    assert matched_source_tools[3] == [
        "deterministic_node_test_script_contract_repair",
        "deterministic_npm_script_contract_repair",
        "deterministic_typescript_local_js_import_repair",
    ]
    assert payload["items"][3]["runtime_plan_rule_ids"] == [
        "javascript.cannot_find_module",
        "javascript.npm_script_typescript_source_require_contract",
        "typescript.local_js_import_extension",
    ]
    assert payload["items"][3]["diagnostic_language"] == "javascript"
    assert matched_source_tools[4] == ["deterministic_javascript_missing_export_repair"]
    assert "deterministic_typescript_missing_export_repair" in matched_source_tools[5]
    assert matched_source_tools[6] == ["deterministic_typescript_vitest_globals_repair"]


def test_javascript_module_error_with_unquoted_export_name_stays_javascript() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    diagnostics = normalize_artifact_quality_errors(
        [
            error,
        ]
    )

    payload = default_repair_rule_registry().coverage(diagnostics).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(error,),
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
                "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
                "src/engine/AlchemyEngine.js": (
                    "class AlchemyEngine {}\n"
                    "function buildDefaultEngine() { return {}; }\n"
                    "module.exports = AlchemyEngine;\n"
                    "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
                    'module.exports.VERSION = "1.0.0";\n'
                ),
            },
        )
    )
    no_stack_probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(
                "SyntaxError: The requested module ./engine/AlchemyEngine.js does not provide an export named default",
            ),
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
                "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
                "src/engine/AlchemyEngine.js": "class AlchemyEngine {}\nmodule.exports = AlchemyEngine;\n",
            },
        )
    )

    assert diagnostics[0].code == "javascript_module_error"
    assert "The requested module ./engine/AlchemyEngine.js" in diagnostics[0].message
    assert payload["items"][0]["diagnostic_language"] == "javascript"
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in payload["items"][0]["matched_source_tools"]
    assert probe.status == "covered_plannable"
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in probe.plannable_source_tools
    assert no_stack_probe.status == "covered_plannable"


def test_plan_probe_passes_coverage_items_as_typed_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    captured: list[PlanDirectorRepairCommandV1] = []

    def fake_plan(command: PlanDirectorRepairCommandV1) -> DirectorRepairPlanningResultV1:
        captured.append(command)
        return DirectorRepairPlanningResultV1(
            ok=False,
            planned=False,
            source_tool=command.source_tool,
            diagnostic_count=len(command.diagnostics),
            error_code="test_probe_planner",
        )

    monkeypatch.setattr(runtime_public_execution, "plan_director_repair", fake_plan)
    result = runtime_public_service.query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=(),
            artifact_quality_issues=tuple(artifact_quality_issues_from_errors([error])),
            base_files={
                "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
                "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
                "src/engine/AlchemyEngine.js": "class AlchemyEngine {}\nmodule.exports = AlchemyEngine;\n",
            },
            source_tools=("deterministic_javascript_esm_commonjs_entrypoint_repair",),
        )
    )

    assert result.items
    assert captured
    command = captured[0]
    assert command.artifact_quality_errors == ()
    assert len(command.diagnostics) == 1
    diagnostic = command.diagnostics[0]
    assert diagnostic.code == "javascript_module_error"
    assert diagnostic.source == "runtime_smoke"
    assert diagnostic.metadata["raw"] == error


def test_materialization_allowed_paths_query_merges_base_and_runtime_changed_paths() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_allowed_paths(
        QueryDirectorRepairMaterializationAllowedPathsV1(
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            base_files=base_files,
            artifact_quality_errors=(error,),
        )
    )

    assert isinstance(result, DirectorRepairMaterializationAllowedPathsResultV1)
    assert result.owner_cell == "director.runtime"
    assert result.execution_boundary == "read_only_materialization_allowed_paths_no_writes"
    assert result.director_tool_execution_required is False
    assert result.planning_result.ok is True
    assert "package.json" in result.allowed_paths
    assert result.changed_paths
    assert set(result.changed_paths).issubset(set(result.allowed_paths))
    assert result.to_dict()["metadata"]["read_only_allowed_paths_plan"] is True


def test_materialization_allowed_paths_query_accepts_typed_artifact_quality_issues() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    typed_issue = artifact_quality_issues_from_errors((error,))[0]
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_allowed_paths(
        QueryDirectorRepairMaterializationAllowedPathsV1(
            source_tool="deterministic_javascript_esm_commonjs_entrypoint_repair",
            base_files=base_files,
            artifact_quality_errors=(),
            artifact_quality_issues=(typed_issue,),
        )
    )

    assert result.planning_result.ok is True
    assert result.planning_result.diagnostics[0].code == "javascript_module_error"
    assert result.planning_result.diagnostics[0].source == "runtime_smoke"
    assert result.changed_paths
    assert set(result.changed_paths).issubset(set(result.allowed_paths))


def test_materialization_plan_probe_query_owns_candidate_and_plannable_source_tools() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    typed_issue = artifact_quality_issues_from_errors((error,))[0]
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(),
            artifact_quality_issues=(typed_issue,),
            base_files=base_files,
            source_tools=(
                "deterministic_javascript_esm_commonjs_entrypoint_repair",
                "deterministic_python_package_shadow_bridge_repair",
            ),
        )
    )

    assert isinstance(result, DirectorRepairMaterializationPlanProbeResultV1)
    assert result.owner_cell == "director.runtime"
    assert result.execution_boundary == "read_only_materialization_plan_probe_no_writes"
    assert result.candidate_source_tools == ("deterministic_javascript_esm_commonjs_entrypoint_repair",)
    assert result.plannable_source_tools == ("deterministic_javascript_esm_commonjs_entrypoint_repair",)
    assert result.plan_probe_result is not None
    assert result.coverage_report.covered_diagnostic_count == 1
    assert result.coverage_report.items[0].diagnostic_code == "javascript_module_error"
    assert result.to_dict()["metadata"]["coverage_is_not_planning"] is True


def test_materialization_plan_probe_query_uses_runtime_schedule_source_tools() -> None:
    error = (
        "Artifact quality scan failed: workspace validation command failed (npm run start): "
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module ./engine/AlchemyEngine.js "
        "does not provide an export named default"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
        "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
        "src/engine/AlchemyEngine.js": (
            "class AlchemyEngine {}\n"
            "function buildDefaultEngine() { return {}; }\n"
            "module.exports = AlchemyEngine;\n"
            "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
        ),
    }

    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(error,),
            base_files=base_files,
            step_id="materialization.typescript_compiler",
        )
    )

    assert isinstance(result, DirectorRepairMaterializationPlanProbeResultV1)
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.requested_source_tools
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.candidate_source_tools
    assert "deterministic_javascript_esm_commonjs_entrypoint_repair" in result.plannable_source_tools
    payload = result.to_dict()
    assert payload["metadata"]["materialization_step_id"] == "materialization.typescript_compiler"
    assert (
        "deterministic_javascript_esm_commonjs_entrypoint_repair"
        in payload["metadata"]["materialization_schedule_source_tools"]
    )


def test_materialization_runtime_probe_plans_direct_node_typescript_import_repair() -> None:
    diagnostic = (
        "npm test failed (exit=1): Error [ERR_MODULE_NOT_FOUND]: Cannot find module "
        "/workspace/src/verify.js imported from /workspace/tests/verify.test.ts"
    )
    base_files = {
        "package.json": '{"type":"module","scripts":{"test":"node --test tests/verify.test.ts"}}\n',
        "tsconfig.json": (
            '{"compilerOptions":{"module":"NodeNext","moduleResolution":"NodeNext"},'
            '"include":["src/**/*.ts"],"exclude":["tests"]}\n'
        ),
        "src/verify.ts": "export function verify(): boolean { return true; }\n",
        "tests/verify.test.ts": (
            'import { verify } from "../src/verify.js";\n'
            'import test from "node:test";\n'
            'test("verify", () => { if (!verify()) throw new Error("failed"); });\n'
        ),
    }

    result = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(diagnostic,),
            base_files=base_files,
            step_id="materialization.target_runtime",
        )
    )

    source_tool = js_syntax.TYPESCRIPT_LOCAL_JS_IMPORT_SOURCE_TOOL
    assert source_tool in result.requested_source_tools
    assert source_tool in result.candidate_source_tools
    assert source_tool in result.plannable_source_tools
    assert result.status == "covered_plannable"
    assert result.plan_probe_result is not None
    assert result.plan_probe_result.covered_unplannable_source_tools == ()


def test_javascript_missing_export_without_declaration_is_covered_unplannable() -> None:
    diagnostics = (
        "file:///tmp/project/src/index.js:1\n"
        "SyntaxError: The requested module './engine/weather.js' does not provide an export named WeatherBalloon",
    )
    base_files = {
        "src/index.js": "import { WeatherBalloon } from './engine/weather.js';\nnew WeatherBalloon().report();\n",
        "src/engine/weather.js": "export function forecast() {\n  return 'cloud';\n}\n",
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_javascript_missing_export_repair",
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()
    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=("deterministic_javascript_missing_export_repair",),
        )
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0
    assert probe.status == "coverage_matched_but_unplannable"
    assert probe.plannable_source_tools == ()
    assert probe.covered_unplannable_source_tools == ("deterministic_javascript_missing_export_repair",)


def test_javascript_missing_export_reexports_existing_imported_binding() -> None:
    diagnostics = (
        "Artifact quality scan failed: unresolved import symbol 'RuleViolationError' "
        "from './engine/runner.js' in src/index.js (sibling module does not define it)",
    )
    base_files = {
        "src/index.js": (
            "import {\n"
            "  runPipeline,\n"
            "  RuleViolationError as _RunnerRuleViolationError, // local facade note\n"
            "} from './engine/runner.js';\n"
            "export { runPipeline };\n"
        ),
        "src/engine/runner.js": (
            "import { RuleViolationError } from './rules.js';\n"
            "export function runPipeline(state) {\n"
            "  if (!state) throw new RuleViolationError('RUNNER', 'state required');\n"
            "  return state;\n"
            "}\n"
        ),
        "src/engine/rules.js": "export class RuleViolationError extends Error {}\n",
    }

    probe = query_director_repair_plan_probe(
        QueryDirectorRepairPlanProbeV1(
            artifact_quality_errors=diagnostics,
            base_files=base_files,
            source_tools=(js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,),
        )
    )
    repaired = _javascript_missing_export_after(
        base_files=base_files,
        diagnostics=diagnostics,
        path="src/engine/runner.js",
    )

    assert probe.status == "covered_plannable"
    assert probe.plannable_source_tools == (js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,)
    assert "export { RuleViolationError };" in repaired
    assert "export function RuleViolationError" not in repaired


def test_javascript_missing_export_typed_cross_artifact_diagnostic_exports_existing_function() -> None:
    diagnostic = RepairDiagnosticV1(
        source="artifact_quality",
        code="cross_artifact_unresolved_import_symbol",
        message="Cross-artifact import symbol is not exported by the resolved owner.",
        path="src/engine/runner.js",
        metadata={
            "symbol": "validateWishShape",
            "module": "./rules.js",
            "contract_plane": "cross_artifact_interface",
            "raw": (
                "Artifact quality scan failed: unresolved import symbol 'validateWishShape' "
                "from './rules.js' in src/engine/runner.js (sibling module does not define it)"
            ),
        },
    )
    base_files = {
        "src/engine/runner.js": (
            'import { validateWishShape, scoreWish } from "./rules.js";\n'
            "export function createRunner() { return { validateWishShape, scoreWish }; }\n"
        ),
        "src/engine/rules.js": (
            "function validateWishShape(wish) {\n"
            "  return wish;\n"
            "}\n\n"
            "export function scoreWish(wish) {\n"
            "  return wish.priority;\n"
            "}\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            diagnostics=(diagnostic,),
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is True
    assert planning["planned"] is True
    assert planning["composition_summary"]["changed_paths"] == ["src/engine/rules.js"]
    repaired = planning["composition_summary"]["patches"][0]["content_after"]
    assert "export function validateWishShape(wish)" in repaired
    assert "export function scoreWish(wish)" in repaired


def test_javascript_missing_run_export_without_declaration_is_covered_unplannable() -> None:
    diagnostics = (
        "Artifact quality scan failed: unresolved import symbol 'run' from '../src/index.js' in tests/test_basic.js",
    )
    base_files = {
        "src/index.js": "console.log('dream note app');\n",
        "tests/test_basic.js": (
            'import { run } from "../src/index.js";\n'
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool="deterministic_javascript_missing_export_repair",
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["changed_paths"] == []


def test_javascript_assertion_failure_does_not_rewrite_exported_domain_functions() -> None:
    diagnostics = (
        "npm test failed: AssertionError [ERR_ASSERTION]: Expected values to be strictly equal: actual 0 expected 1",
    )
    base_files = {
        "src/engine/rules.js": "export function validateDream(value) {\n  return value?.length > 0;\n}\n",
        "tests/product.test.js": (
            "import { validateDream } from \"../src/engine/rules.js\";\nassert.equal(validateDream('moon'), true);\n"
        ),
    }

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_javascript_esm_commonjs_entrypoint_repair_rewrites_multiline_blocks() -> None:
    repaired = _javascript_esm_commonjs_after(
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/models/Note.js": "export class Note {}\nexport default Note;\n",
            "src/index.js": (
                '"use strict";\n\n'
                'const Note = require("./models/Note");\n\n'
                "function main() {\n"
                "  return new Note();\n"
                "}\n\n"
                "if (require.main === module) {\n"
                "  main();\n"
                "}\n\n"
                "module.exports = {\n"
                "  main,\n"
                "  Note,\n"
                "};\n"
                ".exports;\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:3\n"
            "ReferenceError: require is not defined in ES module scope. "
            'package.json contains "type": "module".',
        ),
    )

    assert 'import { Note } from "./models/Note.js";' in repaired
    assert "if (import.meta.url === `file://${process.argv[1]}`)" in repaired
    assert "export { main, Note };" in repaired
    assert "export default { main, Note };" in repaired
    assert "require(" not in repaired
    assert "require.main" not in repaired
    assert "module.exports" not in repaired
    assert ".exports" not in repaired


def test_javascript_esm_commonjs_entrypoint_repair_covers_artifact_quality_static_diagnostic() -> None:
    diagnostic = (
        "Artifact quality scan failed: JavaScript source src/engine/rules.js uses CommonJS runtime syntax; "
        "npm package manifest declares type=module but workspace JavaScript uses CommonJS runtime syntax "
        "in package.json"
    )

    repaired = _javascript_esm_commonjs_after(
        path="src/engine/rules.js",
        base_files={
            "package.json": '{"type":"module","scripts":{"start":"node src/index.js"}}',
            "src/index.js": 'import { buildRuleEngine } from "./engine/rules.js";\n',
            "src/engine/rules.js": (
                "function buildRuleEngine() {\n"
                "  return { ready: true };\n"
                "}\n\n"
                "module.exports = {\n"
                "  buildRuleEngine,\n"
                "};\n"
            ),
        },
        diagnostics=(diagnostic,),
    )

    assert "export { buildRuleEngine };" in repaired
    assert "export default { buildRuleEngine };" in repaired
    assert "module.exports" not in repaired


def test_javascript_esm_commonjs_entrypoint_repair_converts_default_imported_module() -> None:
    repaired = _javascript_esm_commonjs_after(
        path="src/engine/AlchemyEngine.js",
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/index.js": 'import AlchemyEngine from "./engine/AlchemyEngine.js";\n',
            "src/models/Note.js": "export class Note {}\nexport default Note;\n",
            "src/engine/AlchemyEngine.js": (
                '"use strict";\n\n'
                'const Note = require("../models/Note");\n\n'
                "class AlchemyEngine {\n"
                "  constructor() {\n"
                "    this.notes = [new Note()];\n"
                "  }\n"
                "}\n\n"
                "function buildDefaultEngine() {\n"
                "  return { notes: [] };\n"
                "}\n\n"
                "module.exports = AlchemyEngine;\n"
                "module.exports.buildDefaultEngine = buildDefaultEngine;\n"
                'module.exports.VERSION = "1.0.0";\n'
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:1\n"
            "SyntaxError: The requested module './engine/AlchemyEngine.js' "
            "does not provide an export named 'default'",
        ),
    )

    assert 'import { Note } from "../models/Note.js";' in repaired
    assert "export default AlchemyEngine;" in repaired
    assert "export { buildDefaultEngine };" in repaired
    assert 'export const VERSION = "1.0.0";' in repaired
    assert "module.exports" not in repaired
    assert "require(" not in repaired


def test_javascript_esm_commonjs_entrypoint_repair_preserves_namespace_require_binding() -> None:
    repaired = _javascript_esm_commonjs_after(
        base_files={
            "package.json": '{"type":"module","main":"src/index.js","scripts":{"start":"node src/index.js"}}',
            "src/engine/AlchemyEngine.js": (
                "export class AlchemyEngine {}\n"
                "export class Recipe {}\n"
                "export class Note {}\n"
                "export class DreamCard {}\n"
            ),
            "src/index.js": (
                'const AlchemyEngine = require("./engine/AlchemyEngine");\n'
                "const { Note, DreamCard, Recipe } = AlchemyEngine;\n"
                "function buildDemoEngine() {\n"
                "  const engine = new AlchemyEngine();\n"
                "  return { engine, Note, DreamCard, Recipe };\n"
                "}\n"
                "module.exports = { buildDemoEngine };\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm run start): "
            "file:///tmp/project/src/index.js:1\n"
            "ReferenceError: require is not defined in ES module scope\n"
            'package.json contains "type": "module"',
        ),
    )

    assert 'import * as AlchemyEngine from "./engine/AlchemyEngine.js";' in repaired
    assert "const engine = new AlchemyEngine.AlchemyEngine();" in repaired
    assert "const { Note, DreamCard, Recipe } = AlchemyEngine;" in repaired
    assert "export { buildDemoEngine };" in repaired
    assert "require(" not in repaired
    assert "module.exports" not in repaired


def test_javascript_missing_export_repair_does_not_invent_domain_contracts() -> None:
    base_files = {
        "src/index.js": "console.log('dream note app');\n",
        "tests/test_basic.js": (
            'import { run, refineDreamNotes } from "../src/index.js";\n'
            "const result = refineDreamNotes({ notes: ['有效便签', '', null] });\n"
            "assert.equal(result.count, 1);\n"
            "assert.equal(result.distilled[0], '[提炼] 有效便签');\n"
            "const output = run();\n"
            "assert.equal(output.ok, true);\n"
            "assert.match(output.entrypoint, /src[\\\\/]+index\\.js$/);\n"
        ),
    }
    diagnostics = (
        "Artifact quality scan failed: unresolved import symbol 'refineDreamNotes' "
        "from '../src/index.js' in tests/test_basic.js",
        "Artifact quality scan failed: unresolved import symbol 'run' from '../src/index.js' in tests/test_basic.js",
    )

    planning = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=js_syntax.JAVASCRIPT_MISSING_EXPORT_SOURCE_TOOL,
            base_files=base_files,
            artifact_quality_errors=diagnostics,
            mode="shadow",
        )
    ).to_dict()

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_javascript_missing_export_repair_turns_iterable_method_into_constant() -> None:
    repaired = _javascript_missing_export_after(
        path="src/engine/AlchemyEngine.js",
        base_files={
            "src/engine/AlchemyEngine.js": (
                "export class AlchemyEngine {\n  defaultRecipes() {\n    return [{ name: 'starter' }];\n  }\n}\n"
            ),
            "tests/alchemyEngine.test.js": (
                'import { AlchemyEngine, defaultRecipes } from "../src/engine/AlchemyEngine.js";\n'
                "const engine = new AlchemyEngine();\n"
                "for (const recipe of defaultRecipes) engine.addRecipe(recipe);\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: unresolved import symbol 'defaultRecipes' "
            "from '../src/engine/AlchemyEngine.js' in tests/alchemyEngine.test.js",
        ),
    )

    assert "export const defaultRecipes = new AlchemyEngine().defaultRecipes();" in repaired
    assert "export function defaultRecipes" not in repaired
    assert "export class AlchemyEngine" in repaired


def test_javascript_assertion_does_not_replace_wrong_existing_function() -> None:
    planning = _plan_javascript_missing_export(
        base_files={
            "src/index.js": (
                "export function refineDreamNotes(cards) {\n"
                "  if (!Array.isArray(cards)) return [];\n"
                "  return cards;\n"
                "}\n"
            ),
            "tests/smoke.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { refineDreamNotes } from "../src/index.js";\n'
                "const result = refineDreamNotes('a glowing key', 'silent bell', 'paper moon');\n"
                "assert.equal(result.count, 3);\n"
                "assert.equal(result.summary, 'a glowing key | silent bell | paper moon');\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/smoke.test.js:5\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal:\n\n"
            "undefined !== 3",
        ),
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_javascript_assertion_does_not_invent_prefixed_text_and_semver() -> None:
    planning = _plan_javascript_missing_export(
        base_files={
            "package.json": '{"version":"0.2.0"}\n',
            "src/index.js": (
                "function refineDreamNotes(notes) {\n"
                "  return [];\n"
                "}\n\n"
                "export function getVersion(...args) {\n"
                "  return { ok: true };\n"
                "}\n\n"
                "export { refineDreamNotes };\n"
            ),
            "tests/smoke.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { refineDreamNotes, getVersion, VERSION } from "../src/index.js";\n'
                "const result = refineDreamNotes('  first dream  \\n\\n second dream ');\n"
                'assert.equal(result, "[dream] first dream\\n[dream] second dream");\n'
                "assert.throws(() => refineDreamNotes(null), TypeError);\n"
                "const v = getVersion();\n"
                "assert.equal(typeof v, 'string');\n"
                "assert.ok(/^\\d+\\.\\d+\\.\\d+/.test(v));\n"
                "assert.equal(typeof VERSION, 'string');\n"
                "assert.equal(VERSION, getVersion());\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/smoke.test.js:4\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
        ),
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_javascript_missing_export_does_not_invent_app_metadata() -> None:
    planning = _plan_javascript_missing_export(
        base_files={
            "package.json": (
                '{"name":"dream-note-alchemy-furnace","version":"0.1.0","description":"Dream note alchemy CLI"}\n'
            ),
            "src/index.js": "export function getAppInfo() {\n  return { ok: true };\n}\n",
            "tests/version.test.js": (
                'import assert from "node:assert/strict";\n'
                'import { APP_NAME, APP_VERSION, APP_DESCRIPTION, getAppInfo } from "../src/index.js";\n'
                "assert.equal(typeof APP_NAME, 'string');\n"
                "assert.ok(APP_NAME.length > 0);\n"
                "assert.match(APP_VERSION, /^\\d+\\.\\d+\\.\\d+/);\n"
                "assert.equal(typeof APP_DESCRIPTION, 'string');\n"
                "const info = getAppInfo();\n"
                "assert.equal(info.name, APP_NAME);\n"
                "assert.equal(info.version, APP_VERSION);\n"
                "assert.equal(info.description, APP_DESCRIPTION);\n"
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: workspace validation command failed (npm test): "
            "file:///tmp/project/tests/version.test.js:8\n"
            "AssertionError [ERR_ASSERTION]: Expected values to be strictly equal",
            "Artifact quality scan failed: unresolved import symbol 'APP_DESCRIPTION' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'APP_NAME' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'APP_VERSION' "
            "from '../src/index.js' in tests/version.test.js (sibling module does not define it)",
        ),
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_javascript_missing_export_does_not_invent_asserted_literal_or_note_shape() -> None:
    planning = _plan_javascript_missing_export(
        base_files={
            "package.json": '{"name":"dream-note-alchemy-furnace","version":"0.1.0"}\n',
            "src/index.js": "export function main() {\n  return true;\n}\n",
            "tests/test_index.js": (
                'import assert from "node:assert/strict";\n'
                'import { ALCHEMY_FURNACE, refineDreamNote } from "../src/index.js";\n'
                'assert.equal(typeof ALCHEMY_FURNACE, "string");\n'
                'assert.equal(ALCHEMY_FURNACE, "dream-note-alchemy-furnace");\n'
                'const result = refineDreamNote("  flying over paper lanterns  ");\n'
                "assert.deepEqual(result, {\n"
                '  source: "  flying over paper lanterns  ",\n'
                '  refined: "flying over paper lanterns",\n'
                '  tag: "dream-fragment",\n'
                "});\n"
                'const empty = refineDreamNote("   ");\n'
                'assert.equal(empty.source, "   ");\n'
                'assert.equal(empty.refined, "");\n'
                'assert.equal(empty.tag, "empty");\n'
            ),
        },
        diagnostics=(
            "Artifact quality scan failed: unresolved import symbol 'ALCHEMY_FURNACE' "
            "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
            "Artifact quality scan failed: unresolved import symbol 'refineDreamNote' "
            "from '../src/index.js' in tests/test_index.js (sibling module does not define it)",
        ),
    )

    assert planning["ok"] is False
    assert planning["planned"] is False
    assert planning["composition_summary"]["patch_count"] == 0


def test_repair_rule_registry_rejects_duplicate_rule_ids_and_unknown_source_tool() -> None:
    rule = RepairRuleDefinition(
        rule_id="typescript.object_literal_missing_comma",
        source_tool="deterministic_typescript_return_object_semicolon_repair",
        language="typescript",
        phase="quality_repair",
        archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
        diagnostic_codes=("typescript_ts1005",),
        message_terms=(",", "expected"),
    )

    with pytest.raises(ValueError, match="duplicate repair rule_id"):
        RepairRuleRegistry((rule, rule))

    with pytest.raises(ValueError, match="unregistered repair source_tool"):
        RepairRuleDefinition(
            rule_id="typescript.future_rule",
            source_tool="deterministic_future_repair",
            language="typescript",
            phase="quality_repair",
            archetype=RepairArchetype.OBJECT_LITERAL_SYNTAX,
            diagnostic_codes=("typescript_ts9999",),
        )


def test_repair_rule_registry_does_not_overmatch_ts1005_without_comma_expected_message() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts1005",
        message="';' expected.",
        path="src/app.ts",
        raw="src/app.ts(1,1): error TS1005: ';' expected.",
    )

    matches = default_repair_rule_registry().match_diagnostic(diagnostic)

    assert matches == ()


def test_repair_rule_registry_falls_back_to_raw_when_message_is_empty() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts1005",
        message="",
        path="src/app.ts",
        raw="src/app.ts(1,1): error TS1005: ',' expected.",
    )

    matches = default_repair_rule_registry().match_diagnostic(diagnostic)

    assert [match.rule_id for match in matches] == [
        "typescript.hyphenated_identifier",
        "typescript.object_literal_missing_comma",
    ]


def test_repair_plan_scheduler_orders_dependencies_and_fails_closed_on_cycles() -> None:
    first = RepairPlan(
        rule_id="rule.first",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/first.ts", content="export const first = true;\n"),),
        priority=10,
    )
    second = RepairPlan(
        rule_id="rule.second",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/second.ts", content="export const second = true;\n"),),
        priority=1,
        depends_on=("rule.first",),
    )

    schedule = order_repair_plans((second, first))

    assert schedule.cycle_detected is False
    assert [plan.rule_id for plan in schedule.ordered_plans] == ["rule.first", "rule.second"]

    cyclic_first = RepairPlan(
        rule_id="rule.cyclic_first",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/a.ts", content="a\n"),),
        depends_on=("rule.cyclic_second",),
    )
    cyclic_second = RepairPlan(
        rule_id="rule.cyclic_second",
        source_tool="deterministic_typescript_missing_export_repair",
        operations=(RepairOperation(kind="write_file", path="src/b.ts", content="b\n"),),
        depends_on=("rule.cyclic_first",),
    )

    cyclic_schedule = order_repair_plans((cyclic_first, cyclic_second))

    assert cyclic_schedule.cycle_detected is True
    assert cyclic_schedule.ordered_plans == ()
    assert set(cyclic_schedule.blocked_rule_ids) == {"rule.cyclic_first", "rule.cyclic_second"}


def test_repair_convergence_scheduler_records_revalidation_receipt_evidence(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        current = target.read_text(encoding="utf-8")
        diagnostics = () if "export const done = true;" in current else (diagnostic,)
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("npm", "test"),
            exit_code=0 if not diagnostics else 1,
            raw_output_ref=f"runtime/verifier/round-{round_number}.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        if not diagnostics:
            return ()
        return (
            RepairPlan(
                rule_id="typescript.missing_done_export",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const done = true;\n",
                    ),
                ),
                priority=round_number,
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        allowed_paths=(relative_path,),
    )
    payload = result.to_dict()
    receipt = result.receipts[0]

    assert result.status == "converged"
    assert result.converged is True
    assert result.final_diagnostics == ()
    assert result.rounds[0].status == "converged"
    assert receipt.status == "applied"
    assert receipt.round_number == 1
    assert receipt.errors_before == 1
    assert receipt.errors_after == 0
    assert receipt.net_error_reduction == 1
    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.evidence_status == "resolved_evidence"
    assert receipt.revalidation_evidence.resolved_diagnostic_ids == (diagnostic.diagnostic_id,)
    assert payload["receipts"][0]["evidence_status"] == "resolved_evidence"
    assert payload["rounds"][0]["revalidation_evidence"]["evidence_status"] == "resolved_evidence"
    assert payload["rounds"][0]["revalidation_evidence"]["raw_output_ref"] == "runtime/verifier/round-1.log"
    assert payload["receipts"][0]["revalidation_evidence"]["net_error_reduction"] == 1
    assert target.read_text(encoding="utf-8") == "export const done = true;\n"


def test_runtime_convergence_metadata_uses_current_single_repair_entrypoint_key() -> None:
    metadata = runtime_dispatch_module._runtime_convergence_metadata(
        status="converged",
        source_tools=("deterministic_typescript_missing_export_repair",),
        planner_override=False,
    )

    assert metadata["current_single_repair_entrypoint"] == "run_runtime_repair"
    single_entrypoint_keys = sorted(key for key in metadata if key.endswith("_single_repair_entrypoint"))
    assert single_entrypoint_keys == ["current_single_repair_entrypoint"]
    assert metadata["preferred_entrypoint"] == "run_runtime_repair_convergence"


def test_repair_convergence_scheduler_can_use_policy_gated_editor(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    original = "export const pending = true;\n"
    target.write_text(original, encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )
    edit_calls: list[tuple[str, str, str]] = []
    write_calls: list[tuple[str, str]] = []

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del receipts
        current = target.read_text(encoding="utf-8")
        diagnostics = () if "export const done = true;" in current else (diagnostic,)
        return RepairVerifierSnapshot(
            diagnostics=diagnostics,
            command=("npm", "test"),
            exit_code=0 if not diagnostics else 1,
            raw_output_ref=f"runtime/verifier/editor-round-{round_number}.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        del round_number
        if not diagnostics:
            return ()
        start = original.index("pending")
        return (
            RepairPlan(
                rule_id="typescript.precise_pending_export",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="text_replace",
                        path=relative_path,
                        span_start=start,
                        span_end=start + len("pending"),
                        expected="pending",
                        replacement="done",
                    ),
                ),
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        write_calls.append((path, content))
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    def editor(operation: RepairOperation) -> dict[str, object]:
        current = target.read_text(encoding="utf-8")
        assert operation.expected is not None
        assert operation.replacement is not None
        assert operation.span_start is not None
        assert operation.span_end is not None
        assert current[operation.span_start : operation.span_end] == operation.expected
        updated = current[: operation.span_start] + operation.replacement + current[operation.span_end :]
        target.write_text(updated, encoding="utf-8")
        edit_calls.append((operation.path, operation.expected, operation.replacement))
        return {"ok": True, "file": operation.path, "operation": "edit"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        editor=editor,
        allowed_paths=(relative_path,),
    )
    receipt = result.receipts[0]

    assert result.status == "converged"
    assert edit_calls == [(relative_path, "pending", "done")]
    assert write_calls == []
    assert target.read_text(encoding="utf-8") == "export const done = true;\n"
    assert receipt.status == "applied"
    assert receipt.authoritative is True
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.raw_output_ref == "runtime/verifier/editor-round-1.log"
    assert receipt.errors_before == 1
    assert receipt.errors_after == 0


def test_repair_convergence_scheduler_downgrades_failed_revalidation_receipts(tmp_path: Path) -> None:
    relative_path = "src/app.ts"
    target = tmp_path / relative_path
    target.parent.mkdir(parents=True)
    target.write_text("export const pending = true;\n", encoding="utf-8")
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path=relative_path,
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(
            diagnostics=(diagnostic,),
            command=("npm", "test"),
            exit_code=1,
            raw_output_ref="runtime/verifier/failed.log",
        )

    def planner(diagnostics: tuple[RepairDiagnostic, ...], round_number: int) -> tuple[RepairPlan, ...]:
        if round_number > 1:
            return ()
        return (
            RepairPlan(
                rule_id="typescript.incomplete_fix",
                source_tool="deterministic_typescript_missing_export_repair",
                diagnostics=diagnostics,
                operations=(
                    RepairOperation(
                        kind="write_file",
                        path=relative_path,
                        content="export const still_pending = true;\n",
                    ),
                ),
            ),
        )

    def base_files_provider(plan: RepairPlan) -> dict[str, str]:
        del plan
        return {relative_path: target.read_text(encoding="utf-8")}

    def writer(path: str, content: str) -> dict[str, object]:
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path, "operation": "modify"}

    result = RepairConvergenceScheduler(max_rounds=2).run(
        workspace=tmp_path,
        verifier=verifier,
        planner=planner,
        base_files_provider=base_files_provider,
        writer=writer,
        allowed_paths=(relative_path,),
    )
    receipt = result.receipts[0]

    assert result.status == "cycle_detected"
    assert result.metadata["post_check_evidence_complete"] is True
    assert result.metadata["evidence_status_counts"]["failed_evidence"] == 1
    assert result.metadata["evidence_status_counts"]["missing_evidence"] == 0
    assert result.metadata["failed_evidence_receipt_ids"] == [receipt.receipt_id]
    assert result.metadata["failed_evidence_source_tools"] == ["deterministic_typescript_missing_export_repair"]
    assert result.metadata["missing_evidence_receipt_ids"] == []
    assert result.metadata["revalidation_coverage"]["failed_evidence_receipt_ids"] == [receipt.receipt_id]
    assert receipt.status == "failed_revalidation"
    assert receipt.authoritative is False
    assert receipt.evidence_status == "failed_evidence"
    assert receipt.metadata["requires_revalidation"] is False
    assert receipt.revalidation_evidence is not None
    assert receipt.revalidation_evidence.evidence_status == "failed_evidence"
    assert receipt.revalidation_evidence.exit_code == 1
    assert receipt.revalidation_evidence.residual_diagnostic_ids == (diagnostic.diagnostic_id,)
    assert receipt.errors_before == 1
    assert receipt.errors_after == 1
    assert receipt.net_error_reduction == 0


def test_repair_convergence_scheduler_projects_missing_previous_receipt_evidence() -> None:
    pending_receipt = RepairReceipt(
        receipt_id="repair_receipt.pending_missing_evidence",
        plan_id="plan.pending_missing_evidence",
        rule_id="typescript.pending",
        source_tool="deterministic_typescript_missing_export_repair",
        status="pending_revalidation",
        mode="commit",
        authoritative=False,
        files_changed=("src/app.ts",),
        metadata={"requires_revalidation": True},
    )

    def verifier(round_number: int, receipts: tuple[object, ...]) -> RepairVerifierSnapshot:
        del round_number, receipts
        return RepairVerifierSnapshot(diagnostics=(), command=("npm", "test"), exit_code=0)

    result = RepairConvergenceScheduler(max_rounds=1).run(
        workspace=Path("."),
        verifier=verifier,
        planner=lambda _diagnostics, _round_number: (),
        base_files_provider=lambda _plan: {},
        previous_receipts=(pending_receipt,),
    )

    assert result.status == "already_clean"
    assert result.metadata["post_check_evidence_complete"] is False
    assert result.metadata["evidence_status_counts"]["missing_evidence"] == 1
    assert result.metadata["evidence_status_counts"]["failed_evidence"] == 0
    assert result.metadata["missing_evidence_receipt_ids"] == [pending_receipt.receipt_id]
    assert result.metadata["missing_evidence_source_tools"] == ["deterministic_typescript_missing_export_repair"]
    assert result.metadata["failed_evidence_receipt_ids"] == []
    assert result.metadata["revalidation_coverage"]["requires_revalidation"] is True


def test_repair_receipt_revalidation_evidence_is_authoritative_hash_material() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path="src/app.ts",
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )

    def receipt_with(evidence: RepairRevalidationEvidence | None) -> RepairReceipt:
        return RepairReceipt(
            receipt_id="repair_receipt.test",
            plan_id="plan.test",
            rule_id="typescript.missing_done_export",
            source_tool="deterministic_typescript_missing_export_repair",
            status="applied",
            mode="commit",
            authoritative=evidence is not None and evidence.exit_code == 0,
            files_changed=("src/app.ts",),
            operation_ids=("operation.test",),
            diagnostics=(diagnostic,),
            before_hashes={"src/app.ts": "before"},
            after_hashes={"src/app.ts": "after"},
            round_number=1,
            revalidation_evidence=evidence,
        )

    resolved_evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=0,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(),
        errors_before_count=1,
        errors_after_count=0,
        resolved_diagnostic_ids=(diagnostic.diagnostic_id,),
        round_number=1,
        raw_output_ref="runtime/verifier/round-1.log",
    )
    residual_evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=1,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(diagnostic,),
        errors_before_count=1,
        errors_after_count=1,
        residual_diagnostic_ids=(diagnostic.diagnostic_id,),
        round_number=1,
        raw_output_ref="runtime/verifier/round-1.log",
    )

    pending_receipt = receipt_with(None)
    resolved_receipt = receipt_with(resolved_evidence)
    residual_receipt = receipt_with(residual_evidence)
    payload = resolved_receipt.to_dict()

    assert resolved_receipt.authority_hash() != pending_receipt.authority_hash()
    assert resolved_receipt.projection_hash() != pending_receipt.projection_hash()
    assert resolved_receipt.authority_hash() != residual_receipt.authority_hash()
    assert resolved_receipt.projection_hash() != residual_receipt.projection_hash()
    assert resolved_receipt.errors_before == 1
    assert resolved_receipt.errors_after == 0
    assert resolved_receipt.net_error_reduction == 1
    assert pending_receipt.evidence_status == "missing_evidence"
    assert resolved_receipt.evidence_status == "resolved_evidence"
    assert residual_receipt.evidence_status == "failed_evidence"
    assert payload["evidence_status"] == "resolved_evidence"
    assert payload["authority_hash"] == resolved_receipt.authority_hash()
    assert payload["projection_hash"] == resolved_receipt.projection_hash()
    assert payload["revalidation_evidence"]["evidence_status"] == "resolved_evidence"
    assert payload["revalidation_evidence"]["net_error_reduction"] == 1


def test_public_repair_receipt_native_revalidation_fields_round_trip() -> None:
    diagnostic_payload = {
        "source": "artifact_quality",
        "code": "typescript_ts2304",
        "message": "Cannot find name 'done'.",
        "path": "src/app.ts",
        "diagnostic_id": "diag_ts2304_done",
    }
    evidence_payload = {
        "command": ["npm", "test"],
        "exit_code": 0,
        "round_number": 1,
        "evidence_status": "resolved_evidence",
        "errors_before": 1,
        "errors_after": 0,
        "net_error_reduction": 1,
        "resolved_diagnostic_ids": ["diag_ts2304_done"],
        "residual_diagnostic_ids": [],
        "diagnostics_before": [diagnostic_payload],
        "diagnostics_after": [],
        "raw_output_ref": "runtime/verifier/round-1.log",
        "metadata": {"verifier": "npm_test"},
    }

    receipt = RepairReceiptV1(
        receipt_id="repair_receipt.public.native",
        plan_id="plan.public.native",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        authoritative=True,
        files_changed=("src/app.ts",),
        revalidation_evidence=evidence_payload,
    )
    payload = receipt.to_dict()

    assert receipt.evidence_status == "resolved_evidence"
    assert receipt.verifier_command == ("npm", "test")
    assert receipt.verifier_exit_code == 0
    assert receipt.diagnostics_before == (diagnostic_payload,)
    assert receipt.diagnostics_after == ()
    assert receipt.resolved_diagnostic_ids == ("diag_ts2304_done",)
    assert receipt.residual_diagnostic_ids == ()
    assert payload["verifier_command"] == ["npm", "test"]
    assert payload["verifier_exit_code"] == 0
    assert payload["diagnostics_before"] == [diagnostic_payload]
    assert payload["resolved_diagnostic_ids"] == ["diag_ts2304_done"]

    native_only_receipt = RepairReceiptV1(
        receipt_id="repair_receipt.public.native_only",
        plan_id="plan.public.native_only",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        authoritative=True,
        files_changed=("src/app.ts",),
        evidence_status="resolved_evidence",
        errors_before=1,
        errors_after=0,
        net_error_reduction=1,
        verifier_command=("npm", "test"),
        verifier_exit_code=0,
        diagnostics_before=(diagnostic_payload,),
        diagnostics_after=(),
        resolved_diagnostic_ids=("diag_ts2304_done",),
        residual_diagnostic_ids=(),
    )

    assert native_only_receipt.revalidation_evidence["command"] == ["npm", "test"]
    assert native_only_receipt.revalidation_evidence["exit_code"] == 0
    assert native_only_receipt.revalidation_evidence["diagnostics_before"] == [diagnostic_payload]
    assert native_only_receipt.revalidation_evidence["resolved_diagnostic_ids"] == ["diag_ts2304_done"]


def test_repair_receipt_authority_hash_excludes_agi_advisory_projection_material() -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="typescript_ts2304",
        message="Cannot find name 'done'.",
        path="src/app.ts",
        raw="src/app.ts(1,14): error TS2304: Cannot find name 'done'.",
    )
    evidence = RepairRevalidationEvidence(
        command=("npm", "test"),
        exit_code=0,
        diagnostics_before=(diagnostic,),
        diagnostics_after=(),
        resolved_diagnostic_ids=(diagnostic.diagnostic_id,),
    )
    base_receipt = RepairReceipt(
        receipt_id="repair_receipt.advisory",
        plan_id="plan.advisory",
        rule_id="typescript.missing_done_export",
        source_tool="deterministic_typescript_missing_export_repair",
        status="applied",
        mode="commit",
        authoritative=True,
        files_changed=("src/app.ts",),
        operation_ids=("operation.advisory",),
        diagnostics=(diagnostic,),
        before_hashes={"src/app.ts": "before"},
        after_hashes={"src/app.ts": "after"},
        revalidation_evidence=evidence,
    )
    advisory_receipt = RepairReceipt(
        receipt_id=base_receipt.receipt_id,
        plan_id=base_receipt.plan_id,
        rule_id=base_receipt.rule_id,
        source_tool=base_receipt.source_tool,
        status=base_receipt.status,
        mode=base_receipt.mode,
        authoritative=base_receipt.authoritative,
        files_changed=base_receipt.files_changed,
        operation_ids=base_receipt.operation_ids,
        diagnostics=base_receipt.diagnostics,
        before_hashes=base_receipt.before_hashes,
        after_hashes=base_receipt.after_hashes,
        revalidation_evidence=base_receipt.revalidation_evidence,
        advisor_notes=(
            RepairAdvisorNote(
                source="agi",
                message="Advisory only.",
                confidence=0.4,
                suggested_rules=(
                    {
                        "pattern": "missing done export",
                        "fix_template": "export const done = true;",
                        "confidence": 0.4,
                    },
                ),
            ),
        ),
    )

    assert base_receipt.authority_hash() == advisory_receipt.authority_hash()
    assert base_receipt.projection_hash() != advisory_receipt.projection_hash()


def test_patch_composer_applies_text_spans_descending() -> None:
    base = {"src/app.ts": "alpha beta gamma"}
    operations = [
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=0,
            span_end=5,
            expected="alpha",
            replacement="one",
        ),
        RepairOperation(
            kind="text_replace",
            path="src/app.ts",
            span_start=11,
            span_end=16,
            expected="gamma",
            replacement="three",
        ),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert len(result.patches) == 1
    assert result.patches[0].content_after == "one beta three"


def test_patch_composer_fails_closed_on_overlapping_text_spans() -> None:
    base = {"src/app.ts": "abcdef"}
    operations = [
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=1, span_end=4, replacement="X"),
        RepairOperation(kind="text_replace", path="src/app.ts", span_start=3, span_end=5, replacement="Y"),
    ]

    result = PatchComposer().compose(base, operations)

    assert not result.ok
    assert result.issues[0].code == "overlapping_text_spans"


def test_patch_composer_merges_json_operations() -> None:
    base = {"package.json": '{"scripts":{"test":"echo fail"}}\n'}
    operations = [
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "test"), value="npm run build"),
        RepairOperation(kind="json_set", path="package.json", json_path=("scripts", "build"), value="tsc"),
    ]

    result = PatchComposer().compose(base, operations)

    assert result.ok
    assert '"build": "tsc"' in result.patches[0].content_after
    assert '"test": "npm run build"' in result.patches[0].content_after


def test_typescript_object_literal_comma_rule_builds_canonical_plan() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."]
    )

    plan = build_typescript_object_literal_comma_plan(
        base_files={"src/models/Flight.ts": content},
        diagnostics=diagnostics,
    )

    assert plan is not None
    assert plan.rule_id == "typescript.object_literal_missing_comma"
    assert plan.source_tool == "deterministic_typescript_return_object_semicolon_repair"
    assert plan.operations[0].kind == "write_file"
    assert "flightTime, landed:" in str(plan.operations[0].content)
    composition = PatchComposer().compose({"src/models/Flight.ts": content}, plan.operations)
    assert composition.ok
    assert "flightTime, landed:" in composition.patches[0].content_after


def test_typescript_object_literal_comma_runtime_plans_composition_inside_kernel() -> None:
    content = (
        "export function runFlight() {\n"
        "  const samples = [];\n"
        "  const range = 10;\n"
        "  const maxAltitude = 2;\n"
        "  const flightTime = 3;\n"
        "  return { samples, range, maxAltitude, flightTime  landed: undefined as unknown as boolean };\n"
        "}\n"
    )

    planning = plan_typescript_object_literal_comma_repair(
        base_files={"src/models/Flight.ts": content},
        artifact_quality_errors=[
            "TypeScript syntax check failed: src/models/Flight.ts(6,47): error TS1005: ',' expected."
        ],
        mode="shadow",
    )

    assert planning.plan is not None
    assert planning.plan.rule_id == "typescript.object_literal_missing_comma"
    assert planning.plan.mode == "shadow"
    assert planning.composition is not None
    assert planning.composition.ok is True
    assert "flightTime, landed:" in planning.composition.patches[0].content_after


def test_typescript_hyphenated_identifier_rule_repairs_declaration_and_uses() -> None:
    content = (
        "export function checkScripts(scripts: Record<string, string>) {\n"
        "  const hasSample-check = Object.values(scripts).some((value) => /DONE/.test(value));\n"
        "  return !hasSample-check;\n"
        "}\n"
    )
    diagnostics = normalize_artifact_quality_errors(
        ["TypeScript syntax check failed: src/verify.ts(2,18): error TS1005: ',' expected."]
    )

    plan = build_typescript_hyphenated_identifier_plan(
        base_files={"src/verify.ts": content},
        diagnostics=diagnostics,
        mode="shadow",
    )

    assert plan is not None
    assert plan.rule_id == "typescript.hyphenated_identifier"
    assert plan.source_tool == ts_syntax.TYPESCRIPT_HYPHENATED_IDENTIFIER_SOURCE_TOOL
    assert len(plan.operations) == 1
    operation = plan.operations[0]
    assert operation.kind == "text_replace"
    assert operation.path == "src/verify.ts"
    assert "const hasSampleCheck =" in str(operation.replacement)
    assert "return !hasSampleCheck;" in str(operation.replacement)
    assert "hasSample-check" in str(operation.expected)
    assert "hasSample-check" not in str(operation.replacement)
