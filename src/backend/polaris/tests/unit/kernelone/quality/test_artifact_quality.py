from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from polaris.kernelone.quality import (
    ArtifactQualityIssue,
    artifact_quality as artifact_quality_module,
    artifact_quality_issues_from_errors,
    scan_workspace_artifact_quality,
    scan_workspace_artifact_quality_evidence,
)


def _write_trivial_test(path: Path, *, count: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(count)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scoped_scan_detects_placeholder_tests_in_target_file(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["tests/unit/card-rules.test.ts"],
    )

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scoped_scan_detects_recursive_npm_package_script(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"npm run build","test":"npm run build"}}\n',
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm package manifest script 'build' recursively invokes itself via build -> build" in errors[0]


def test_scoped_scan_ignores_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")
    changed = tmp_path / "src" / "feature.ts"
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("export const feature = true;\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/feature.ts"],
    )

    assert errors == []


def test_full_scan_detects_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path))

    assert errors
    assert "tests/unit/legacy.test.ts" in errors[0]


def test_scoped_scan_expands_declared_directory_targets(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["tests"])

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scan_detects_generated_structural_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "generated.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const note = 'structural build passed';\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/generated.ts"])

    assert errors
    assert "structural build passed" in errors[0]


def test_file_scanner_only_fallback_parses_residual_string_errors(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "src" / "client" / "generated.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const note = 'structural build passed';\n",
        encoding="utf-8",
    )
    parsed_string_errors: list[str] = []
    original = artifact_quality_module._artifact_quality_issues_from_errors

    def _capture_string_fallback(values: Iterable[Any] | str) -> Any:
        rows = tuple(values) if not isinstance(values, str) else (values,)
        parsed_string_errors.extend(str(item) for item in rows if isinstance(item, str) and item)
        return original(rows)

    monkeypatch.setattr(
        artifact_quality_module,
        "_artifact_quality_issues_from_errors",
        _capture_string_fallback,
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["src/client/generated.ts"],
    )

    assert evidence.errors
    assert "structural build passed" in evidence.errors[0]
    assert parsed_string_errors == []
    assert evidence.issues[0].code == "deterministic_scaffold_marker"


def test_scan_detects_tool_receipt_contamination_without_echoing_receipt(tmp_path: Path) -> None:
    target = tmp_path / "tests" / "simulation.test.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "**write_file**: Error - {'ok': False, 'error': 'Destructive shrink rejected: "
        "this edit would replace tests/garden.test.ts'}\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["tests/simulation.test.ts"])

    assert errors == [
        "Artifact quality scan failed: tool execution receipt contamination in tests/simulation.test.ts; "
        "file contains a Polaris tool failure receipt instead of source code. "
        "Rewrite this artifact with real UTF-8 project code and do not copy tool error text."
    ]
    assert "Destructive shrink rejected" not in errors[0]
    assert "tests/garden.test.ts" not in errors[0]


def test_tool_receipt_contamination_threads_direct_typed_issue_without_fallback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "tests" / "simulation.test.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("**write_file**: Error - {'ok': False, 'error': 'write failed'}\n", encoding="utf-8")
    parsed_string_errors: list[str] = []
    original = artifact_quality_module._artifact_quality_issues_from_errors

    def _capture_string_fallback(values: Iterable[Any] | str) -> Any:
        rows = tuple(values) if not isinstance(values, str) else (values,)
        parsed_string_errors.extend(str(item) for item in rows if isinstance(item, str) and item)
        return original(rows)

    monkeypatch.setattr(
        artifact_quality_module,
        "_artifact_quality_issues_from_errors",
        _capture_string_fallback,
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["tests/simulation.test.ts"],
    )

    assert evidence.errors
    assert parsed_string_errors == []
    assert evidence.issues[0].code == "tool_receipt_contamination"
    assert evidence.issues[0].metadata["raw"] == evidence.errors[0]


def test_scan_detects_source_narration_contamination(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "I'll address the quality repair issues immediately.\nexport const ready = true;\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert errors == [
        "Artifact quality scan failed: source narration contamination in src/main.ts; "
        "file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    ]


def test_source_narration_contamination_threads_direct_typed_issue_without_fallback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "I'll address the quality repair issues immediately.\nexport const ready = true;\n",
        encoding="utf-8",
    )
    parsed_string_errors: list[str] = []
    original = artifact_quality_module._artifact_quality_issues_from_errors

    def _capture_string_fallback(values: Iterable[Any] | str) -> Any:
        rows = tuple(values) if not isinstance(values, str) else (values,)
        parsed_string_errors.extend(str(item) for item in rows if isinstance(item, str) and item)
        return original(rows)

    monkeypatch.setattr(
        artifact_quality_module,
        "_artifact_quality_issues_from_errors",
        _capture_string_fallback,
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["src/main.ts"])

    assert evidence.errors
    assert parsed_string_errors == []
    assert evidence.issues[0].code == "source_narration_contamination"
    assert evidence.issues[0].metadata["raw"] == evidence.errors[0]


def test_scan_detects_repair_directive_narration_contamination(tmp_path: Path) -> None:
    target = tmp_path / "src" / "models" / "moonphase.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "The repair directive is clear: create the missing module imported by src/index.ts.\n"
        "For moonphase.ts - should export moon phase related types/classes.\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/models/moonphase.ts"])

    assert errors == [
        "Artifact quality scan failed: source narration contamination in src/models/moonphase.ts; "
        "file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    ]


def test_scan_detects_quality_repair_mode_narration_contamination(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "The quality repair mode requires me to create the missing files. Let me analyze what's needed:\n"
        "1. `src/main.ts` - Missing target file\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert errors == [
        "Artifact quality scan failed: source narration contamination in src/main.ts; "
        "file starts with assistant prose instead of project source code. "
        "Rewrite this artifact with real UTF-8 source only."
    ]


def test_scan_detects_generic_typescript_project_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('console.log("Hello from TypeScript project.");\n', encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert any("Hello from TypeScript project" in error for error in errors)


def test_scan_detects_audit_seed_scenario_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "game" / "rules-engine.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export const cardRulesEngineScenario0 = {
  title: "card-rules-engine planning scenario 0",
  tags: ["planning", "draft", "audit-seed"],
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/game/rules-engine.ts"])

    assert errors
    assert "audit-seed" in errors[0] or "planning scenario" in errors[0]


def test_scan_detects_semicolon_terminated_typescript_object_values(tmp_path: Path) -> None:
    target = tmp_path / "src" / "models" / "firefly.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export function toJSON() {
  return {
    id: "firefly-1",
    moonSensitivity: 0.8;
  };
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/models/firefly.ts"])

    assert any("semicolon-terminated property" in error and "src/models/firefly.ts" in error for error in errors)


def test_scan_does_not_apply_typescript_return_object_semicolon_rule_to_javascript(tmp_path: Path) -> None:
    target = tmp_path / "src" / "engine" / "runner.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export function dispatch(result) {
  return {
    generatedAt: result.generatedAt,
    queue: result.queue,
    dropped: result.dropped,
  };
}

export const engineApi = Object.freeze({
  dispatch,
});
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/engine/runner.js"])

    assert not any("semicolon-terminated property" in error for error in errors)


def test_scan_treats_node_scheme_imports_as_node_builtins_for_javascript(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
    target = tmp_path / "src" / "engine" / "runner.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

export function load(name) {
  return require(name);
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/engine/runner.js"])

    assert not any("undeclared runtime import 'node:module'" in error for error in errors)


def test_scan_detects_structural_verification_scripts(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "build.mjs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "console.log(`build verification completed: ${required.length} files`);\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["scripts/build.mjs"])

    assert errors
    assert "build verification completed" in errors[0]


def test_scan_detects_npm_default_failing_test_script(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 1"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_scan_detects_npm_no_test_specified_even_when_exit_code_is_zero(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_package_manifest_scanner_threads_direct_typed_issue_before_projection(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"Error: no test specified\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    captured_issue_raws: list[str] = []
    original = artifact_quality_module._package_manifest_evidence_from_errors

    def _capture_projection_inputs(
        errors: list[str],
        relative_path: str,
        direct_issues: list[artifact_quality_module.ArtifactQualityIssue] | None = None,
    ) -> Any:
        captured_issue_raws.extend(
            str((issue.metadata or {}).get("raw") or issue.message).strip() for issue in direct_issues or []
        )
        return original(errors, relative_path, direct_issues)

    monkeypatch.setattr(
        artifact_quality_module,
        "_package_manifest_evidence_from_errors",
        _capture_projection_inputs,
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors
    assert "npm default failing test script" in evidence.errors[0]
    assert captured_issue_raws == list(evidence.errors)
    assert len(evidence.issues) == len(evidence.errors)
    assert all(issue.source == "package_manifest_scanner" for issue in evidence.issues)


def test_scan_detects_npm_no_tests_specified_plural(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"No tests specified\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm default failing test script" in errors[0]


def test_scan_detects_npm_no_tests_yet_placeholder(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"No tests yet\\" && exit 0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm placeholder test script" in errors[0]


def test_scan_detects_npm_all_tests_passed_placeholder(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "echo \\"All tests passed\\""
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors
    assert "npm placeholder test script" in errors[0]


def test_scan_detects_npm_placeholder_start_script(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "garden-engine",
  "version": "1.0.0",
  "scripts": {
    "start": "echo \\"Open index.html in a browser\\""
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("script 'start' is a placeholder command" in error for error in errors)


def test_scan_detects_npm_build_script_that_swallows_failures(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "garden-engine",
  "version": "1.0.0",
  "scripts": {
    "build": "tsc --noEmit 2>/dev/null || echo \\"TypeScript check skipped\\""
  },
  "devDependencies": {
    "typescript": "^5.6.0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("script 'build' swallows command failures" in error for error in errors)


def test_scan_detects_npm_script_missing_local_config_file(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "jest --config jest.config.js --forceExit"
  },
  "jest": {
    "testEnvironment": "node"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert evidence.errors
    assert any("references missing config file 'jest.config.js'" in error for error in evidence.errors)

    config_issues = [issue for issue in evidence.issues if issue.code == "npm_script_missing_local_config"]
    assert config_issues, "evidence.issues must contain a typed npm_script_missing_local_config entry"
    assert len(config_issues) == 1

    config_issue: ArtifactQualityIssue = config_issues[0]
    assert config_issue.source == "npm_script_config_scanner"
    assert config_issue.path == "package.json"
    assert config_issue.severity == "error"
    assert config_issue.line is None
    assert config_issue.column is None

    metadata = dict(config_issue.metadata or {})
    assert metadata["script_issue"] == "missing_local_config"
    assert metadata["script_issue_source"] == "npm_script_config_scanner"
    assert metadata["script_name"] == "test"
    assert metadata["config_path"] == "jest.config.js"
    assert metadata["manifest_path"] == "package.json"
    assert "references missing config file 'jest.config.js'" in str(metadata.get("raw") or "")

    legacy_error = next(
        error for error in evidence.errors if "references missing config file 'jest.config.js'" in error
    )
    assert legacy_error in evidence.errors


def test_artifact_quality_issues_from_errors_projects_npm_script_missing_local_config(
) -> None:
    """Public projection must classify scanner metadata without message parsing."""

    projected = artifact_quality_issues_from_errors(
        [
            {
                "message": "scanner-owned structured payload",
                "path": "package.json",
                "source": "npm_script_config_scanner",
                "metadata": {
                    "raw": "human display only",
                    "manifest_path": "package.json",
                    "script_issue": "missing_local_config",
                    "script_issue_source": "npm_script_config_scanner",
                    "script_name": "test",
                    "config_path": "jest.config.js",
                },
            }
        ]
    )

    assert [payload["code"] for payload in projected] == ["npm_script_missing_local_config"]

    projected_issue = projected[0]
    assert projected_issue["source"] == "npm_script_config_scanner"
    assert projected_issue["path"] == "package.json"
    assert projected_issue["severity"] == "error"

    projected_metadata = dict(projected_issue["metadata"] or {})
    assert projected_metadata["script_issue"] == "missing_local_config"
    assert projected_metadata["script_issue_source"] == "npm_script_config_scanner"
    assert projected_metadata["script_name"] == "test"
    assert projected_metadata["config_path"] == "jest.config.js"
    assert projected_metadata["manifest_path"] == "package.json"


def test_artifact_quality_issues_from_errors_projects_npm_script_missing_local_entrypoint() -> None:
    """Public projection must classify scanner metadata without message parsing."""

    projected = artifact_quality_issues_from_errors(
        [
            {
                "message": "scanner-owned structured payload",
                "path": "package.json",
                "source": "npm_script_entrypoint_scanner",
                "metadata": {
                    "raw": "human display only",
                    "manifest_path": "package.json",
                    "script_issue": "missing_local_entrypoint",
                    "script_issue_source": "npm_script_entrypoint_scanner",
                    "script_name": "start",
                    "entrypoint": "dist/main.js",
                },
            }
        ]
    )

    assert [payload["code"] for payload in projected] == ["npm_script_missing_local_entrypoint"]

    projected_issue = projected[0]
    assert projected_issue["source"] == "npm_script_entrypoint_scanner"
    assert projected_issue["path"] == "package.json"
    assert projected_issue["severity"] == "error"

    projected_metadata = dict(projected_issue["metadata"] or {})
    assert projected_metadata["script_issue"] == "missing_local_entrypoint"
    assert projected_metadata["script_issue_source"] == "npm_script_entrypoint_scanner"
    assert projected_metadata["script_name"] == "start"
    assert projected_metadata["entrypoint"] == "dist/main.js"
    assert projected_metadata["manifest_path"] == "package.json"
    assert projected_metadata["raw"] == "human display only"


def test_scan_detects_standalone_manifest_check_passed_test_script(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "garden-engine",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"const p=require('./package.json');if(!p.main||!p.scripts){process.exit(1)}console.log('Manifest check passed')\\""
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("manifest-only test script" in error for error in errors)


def test_scan_detects_start_script_missing_local_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "start": "node dist/main.js",
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(str(tmp_path), relative_paths=["package.json"])

    assert any("references missing local entrypoint 'dist/main.js'" in error for error in evidence.errors)

    entrypoint_issues = [
        issue
        for issue in evidence.issues
        if issue.code == "npm_script_missing_local_entrypoint"
        and dict(issue.metadata or {}).get("script_name") == "start"
    ]
    assert len(entrypoint_issues) == 1

    entrypoint_issue: ArtifactQualityIssue = entrypoint_issues[0]
    assert entrypoint_issue.source == "npm_script_entrypoint_scanner"
    assert entrypoint_issue.path == "package.json"
    assert entrypoint_issue.severity == "error"
    assert entrypoint_issue.line is None
    assert entrypoint_issue.column is None

    metadata = dict(entrypoint_issue.metadata or {})
    assert metadata["script_issue"] == "missing_local_entrypoint"
    assert metadata["script_issue_source"] == "npm_script_entrypoint_scanner"
    assert metadata["script_name"] == "start"
    assert metadata["entrypoint"] == "dist/main.js"
    assert metadata["manifest_path"] == "package.json"
    assert "references missing local entrypoint 'dist/main.js'" in str(metadata.get("raw") or "")


def test_scan_detects_start_script_direct_tsc_then_missing_dist_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "start": "tsc && node dist/main.js",
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("references missing local entrypoint 'dist/main.js'" in error for error in errors)


def test_scan_detects_bun_start_script_missing_local_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "start": "bun run src/main.ts"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("references missing local entrypoint 'src/main.ts'" in error for error in errors)


def test_scan_detects_deno_start_script_missing_local_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "start": "deno run --allow-read src/main.ts"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("references missing local entrypoint 'src/main.ts'" in error for error in errors)


def test_scan_detects_test_script_missing_local_entrypoint_after_node_loader(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "moon-garden",
  "version": "1.0.0",
  "scripts": {
    "test": "node --loader ts-node/esm src/verify.ts || exit 1"
  },
  "devDependencies": {
    "ts-node": "^10.9.2"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("references missing local entrypoint 'src/verify.ts'" in error for error in errors)


def test_scan_allows_node_test_runner_glob_patterns(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "meteor-wish-queue",
  "version": "1.0.0",
  "scripts": {
    "test": "node --test tests/*.test.js",
    "test:watch": "node --test --watch tests/*.test.js"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert not any("missing local entrypoint" in error for error in errors)


def test_scan_allows_start_script_that_compiles_before_dist_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "compile": "tsc",
    "start": "npm run compile && node dist/main.js",
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert not any("missing local entrypoint" in error for error in errors)


def test_scan_allows_start_script_that_builds_before_dist_entrypoint(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "scripts": {
    "start": "npm run build && node dist/main.js",
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert not any("missing local entrypoint" in error for error in errors)


def test_scan_detects_node_test_runner_without_test_files_when_sources_exist(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "vitest run"
  },
  "devDependencies": {
    "vitest": "^1.6.1"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "services" / "taskgraph.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export class TaskGraph {}\n", encoding="utf-8")
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("test runner script but no test/spec files exist" in error for error in errors)


def test_scan_detects_node_test_directory_script_even_when_tests_exist(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "dream-note-alchemy-furnace",
  "version": "1.0.0",
  "scripts": {
    "test": "node --test tests"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "index.js"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export const ready = true;\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "smoke.test.js"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("import test from 'node:test';\ntest('smoke', () => {});\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("references test directory 'tests' instead of concrete test files" in error for error in errors)


def test_scan_detects_type_module_commonjs_runtime_mismatch(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "dream-note-alchemy-furnace",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "start": "node src/index.js",
    "test": "node --test tests/*.test.js"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "index.js"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        'const Note = require("./models/Note");\nconsole.log(Note.name);\n',
        encoding="utf-8",
    )
    test_file = tmp_path / "tests" / "smoke.test.js"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("import test from 'node:test';\ntest('smoke', () => {});\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any(
        "declares type=module but workspace JavaScript uses CommonJS runtime syntax" in error for error in errors
    )
    assert any("src/index.js uses CommonJS runtime syntax" in error for error in errors)


def test_scan_allows_type_module_with_esm_source(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "dream-note-alchemy-furnace",
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "start": "node src/index.js",
    "test": "node --test tests/*.test.js"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "index.js"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export const ready = true;\nconsole.log(ready);\n", encoding="utf-8")
    test_file = tmp_path / "tests" / "smoke.test.js"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("import test from 'node:test';\ntest('smoke', () => {});\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert not any("CommonJS runtime syntax" in error for error in errors)


def test_scan_detects_manifest_only_npm_test_script(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"const fs=require('fs');const pkg=JSON.parse(fs.readFileSync('package.json','utf8'));if(!pkg.name||!pkg.version) throw new Error('invalid package manifest');console.log('package manifest check passed');\\" --"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("manifest-only test script" in error for error in errors)


def test_scan_detects_dist_only_npm_test_script(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"const fs=require('fs');const p=require('./package.json');if(!fs.existsSync('dist/main.js')){console.error('FAIL: dist/main.js not found');process.exit(1);}if(!p.scripts.build){console.error('FAIL: missing build script');process.exit(1);}console.log('OK');\\""
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("manifest-only test script" in error for error in errors)


def test_scan_detects_structural_node_e_scaffold_test_script(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-bootstrap",
  "version": "1.0.0",
  "scripts": {
    "test": "node -e \\"const fs=require('fs'); const ts=fs.readFileSync('tsconfig.json','utf8'); const src=fs.readFileSync('src/main.ts','utf8'); if(!ts.includes('compilerOptions')) throw new Error('tsconfig missing compilerOptions'); if(!src.includes('console')) throw new Error('main.ts has no output'); console.log('All checks passed');\\""
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("manifest-only test script" in error for error in errors)


def test_scan_allows_self_contained_node_test_script_without_test_files(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        """
{
  "name": "typescript-project",
  "version": "1.0.0",
  "scripts": {
    "test": "node scripts/test.mjs"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    source = tmp_path / "src" / "services" / "taskgraph.ts"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("export class TaskGraph {}\n", encoding="utf-8")
    test_script = tmp_path / "scripts" / "test.mjs"
    test_script.parent.mkdir(parents=True, exist_ok=True)
    test_script.write_text("console.log('ok');\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert errors == []


def test_scan_detects_return_object_property_semicolon(tmp_path: Path) -> None:
    target = tmp_path / "src" / "models" / "task.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export function summary() {
  const lanes: Record<string, number> = {};
  return {
    total: 1,
    lanes;
  };
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/models/task.ts"])

    assert errors
    assert "semicolon-terminated property" in errors[0]


def test_scan_detects_typescript_zod_type_class_name_collision(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"dependencies":{"zod":"^3.23.8"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "models" / "task_definition.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
import { z } from 'zod';

export const TaskDefinitionSchema = z.object({
  name: z.string(),
});

type TaskDefinition = z.infer<typeof TaskDefinitionSchema>;

export class TaskDefinition {
  constructor(public data: TaskDefinition) {}
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/models/task_definition.ts"],
    )

    assert errors
    assert "TypeScript zod inferred type collides with class TaskDefinition" in errors[0]


def test_scan_detects_python_runtime_masquerading_as_npm_manifest(tmp_path: Path) -> None:
    target = tmp_path / "package.json"
    target.write_text(
        """
{
  "name": "web-e2e-workspace",
  "version": "1.0.0",
  "main": "src/main.py",
  "scripts": {
    "start": "python src/main.py"
  },
  "dependencies": {
    "pytest": "^7.0.0"
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("Python runtime entrypoint" in error for error in errors)
    assert any("Python command" in error for error in errors)
    assert any("Python package dependency 'pytest'" in error for error in errors)


def test_scan_detects_unresolved_runtime_typescript_imports(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { RequestContext } from '../context';\n"
        "export const tenantMiddleware = true;\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("undeclared runtime import 'express'" in error for error in errors)
    assert any("unresolved relative import '../context'" in error for error in errors)


def test_scan_requires_node_types_for_typescript_builtin_import(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},"dependencies":{"express":"^4.18.2"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "export const tenantContext = new AsyncLocalStorage<Map<string, string>>();\n"
        "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void { next(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("requires '@types/node'" in error for error in errors)


def test_scan_detects_typescript_project_typecheck_failure(tmp_path: Path, monkeypatch) -> None:
    from types import SimpleNamespace

    from polaris.kernelone.quality import artifact_quality as aq

    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"strict":true},"include":["src/**/*.ts"]}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "config.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export const flowers: Map<string, string> = [];\n", encoding="utf-8")

    def fake_run(*args, **kwargs):
        assert args[0] == ["tsc", "--noEmit", "--pretty", "false"]
        assert kwargs["cwd"] == str(tmp_path)
        return SimpleNamespace(
            returncode=2,
            stdout="src/config.ts(1,45): error TS2740: Type 'undefined[]' is missing Map methods.\n",
            stderr="",
        )

    monkeypatch.setattr(aq, "_typescript_project_typecheck_command", lambda root: "tsc")
    monkeypatch.setattr(aq.subprocess, "run", fake_run)

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/config.ts"])

    assert any("TypeScript project typecheck failed" in error and "src/config.ts" in error for error in errors)


def test_scan_skips_global_tsc_when_package_declares_uninstalled_typescript(tmp_path: Path, monkeypatch) -> None:
    from polaris.kernelone.quality import artifact_quality as aq

    (tmp_path / "package.json").write_text(
        '{"devDependencies":{"typescript":"^5.3.0"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"moduleResolution":"bundler"},"include":["src/**/*.ts"]}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export const ok = true;\n", encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("global tsc must not run when project declares its own TypeScript dependency")

    monkeypatch.setattr(aq.shutil, "which", lambda name: "tsc" if name == "tsc" else None)
    monkeypatch.setattr(aq.subprocess, "run", fail_if_called)

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert errors == []


def test_scan_skips_global_tsc_when_node_resolution_project_declares_uninstalled_typescript(
    tmp_path: Path, monkeypatch
) -> None:
    from polaris.kernelone.quality import artifact_quality as aq

    (tmp_path / "package.json").write_text(
        '{"devDependencies":{"typescript":"^5.4.0"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"moduleResolution":"node","skipLibCheck":true},"include":["src/**/*.ts"]}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export const ok = true;\n", encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("global tsc must not run when project declares an uninstalled TypeScript dependency")

    monkeypatch.setattr(aq.shutil, "which", lambda name: "tsc" if name == "tsc" else None)
    monkeypatch.setattr(aq.subprocess, "run", fail_if_called)

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert errors == []


def test_scan_requires_typescript_dependency_for_package_tsc_without_global_tsc(tmp_path: Path, monkeypatch) -> None:
    from polaris.kernelone.quality import artifact_quality as aq

    (tmp_path / "package.json").write_text(
        '{"scripts":{"build":"tsc","test":"npm run build"}}\n',
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"moduleResolution":"node"},"include":["src/**/*.ts"]}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("export const ok = true;\n", encoding="utf-8")

    def fail_if_called(*args, **kwargs):
        raise AssertionError("global tsc must not run for package-managed TypeScript projects")

    monkeypatch.setattr(aq.shutil, "which", lambda name: "/usr/bin/tsc" if name == "tsc" else None)
    monkeypatch.setattr(aq.subprocess, "run", fail_if_called)

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json", "src/main.ts"])

    assert any("requires 'typescript' devDependency" in error for error in errors)


def test_scan_flags_npm_script_shell_command_substitution(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{"scripts":{"test":"node -e \\"console.error(`Missing value`);\\""}}\n',
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

    assert any("script 'test' uses shell command substitution" in error for error in errors)


def test_scan_detects_html_typescript_module_script(tmp_path: Path) -> None:
    target = tmp_path / "index.html"
    target.write_text(
        """
<!doctype html>
<html>
  <body>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["index.html"])

    assert any("HTML module script references TypeScript source" in error for error in errors)


def test_scan_detects_isolated_modules_type_reexport_without_export_type(tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text(
        '{"compilerOptions":{"isolatedModules":true}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "main.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
interface Firefly {
  id: string;
}

const DEFAULT_FIREFLY: Firefly = { id: "ff-1" };

export {
  Firefly,
  DEFAULT_FIREFLY,
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/main.ts"])

    assert any("isolatedModules requires `export type` for Firefly" in error for error in errors)


def test_scan_allows_node_builtin_import_when_node_types_declared(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},'
        '"dependencies":{"express":"^4.18.2"},"devDependencies":{"@types/node":"^22.10.0"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { Request, Response, NextFunction } from 'express';\n"
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "export const tenantContext = new AsyncLocalStorage<Map<string, string>>();\n"
        "export function tenantMiddleware(req: Request, res: Response, next: NextFunction): void { next(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert errors == []


def test_scan_detects_escaped_newline_that_comments_out_typescript_export(tmp_path: Path) -> None:
    package_json = tmp_path / "package.json"
    package_json.write_text(
        '{"name":"tenant-workspace","scripts":{"test":"node scripts/test.mjs"},'
        '"dependencies":{"express":"^4.18.2"},"devDependencies":{"@types/node":"^22.10.0"}}\n',
        encoding="utf-8",
    )
    target = tmp_path / "src" / "middleware" / "tenant.middleware.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "import { AsyncLocalStorage } from 'async_hooks';\n"
        "// Context for tenant request lifecycle\\nexport const tenantContext = "
        "new AsyncLocalStorage<{ tenantId: string }>();\n"
        "export function tenantMiddleware(): void { tenantContext.getStore(); }\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/middleware/tenant.middleware.ts"],
    )

    assert any("escaped newline in line comment" in error for error in errors)


def test_scan_resolves_dotted_typescript_relative_import_stems(tmp_path: Path) -> None:
    dag_service = tmp_path / "src" / "services" / "dag.service.ts"
    dag_service.parent.mkdir(parents=True, exist_ok=True)
    dag_service.write_text("export class DagService {}\n", encoding="utf-8")
    task_service = tmp_path / "src" / "services" / "task.service.ts"
    task_service.write_text(
        "import { DagService } from './dag.service';\nexport const taskService = new DagService();\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/services/task.service.ts"],
    )

    assert errors == []


def test_scan_detects_patch_residue_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "assets" / "card-assets.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const assetReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/assets/card-assets.ts"])

    assert errors
    assert "patch residue marker" in errors[0]


def test_scan_detects_repeated_numeric_helper_filler(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "feature.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            f"export function featureHelper{index}(value: number): number {{ return value + {index}; }}"
            for index in range(6)
        )
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/feature.ts"])

    assert errors
    assert "numeric helper filler" in errors[0]


def test_repeated_numeric_helper_filler_threads_direct_typed_issue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "client" / "feature.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            f"export function featureHelper{index}(value: number): number {{ return value + {index}; }}"
            for index in range(6)
        )
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["src/client/feature.ts"],
    )

    assert evidence.errors
    helper_issues = [issue for issue in evidence.issues if issue.code == "repeated_numeric_helper_filler"]
    assert helper_issues, "file-level scanner must emit typed repeated_numeric_helper_filler issue"
    helper_issue = helper_issues[0]
    assert helper_issue.source == "file_artifact_scanner"
    assert helper_issue.path == "src/client/feature.ts"
    assert helper_issue.metadata["helper_count"] == 6
    raw = str(helper_issue.metadata.get("raw") or "")
    assert "numeric helper filler" in raw, "metadata.raw must include legacy error string"


def test_patch_residue_marker_threads_direct_typed_issue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "assets" / "card-assets.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const assetReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["src/assets/card-assets.ts"],
    )

    assert evidence.errors
    patch_issues = [issue for issue in evidence.issues if issue.code == "patch_residue_marker"]
    assert patch_issues, "file-level scanner must emit typed patch_residue_marker issue"
    patch_issue = patch_issues[0]
    assert patch_issue.source == "file_artifact_scanner"
    assert patch_issue.path == "src/assets/card-assets.ts"
    metadata = dict(patch_issue.metadata or {})
    assert metadata["marker_kind"] == "patch_residue"
    assert metadata["marker_value"] == ">>>> REPLACE"
    raw = str(metadata.get("raw") or "")
    assert "patch residue marker" in raw, "metadata.raw must include legacy error string"


def test_deterministic_scaffold_marker_threads_direct_typed_issue(
    tmp_path: Path,
) -> None:
    target = tmp_path / "src" / "client" / "scaffold.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const note = 'structural build passed';\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["src/client/scaffold.ts"],
    )

    assert evidence.errors
    scaffold_issues = [issue for issue in evidence.issues if issue.code == "deterministic_scaffold_marker"]
    assert scaffold_issues, "file-level scanner must emit typed deterministic_scaffold_marker issue"
    scaffold_issue = scaffold_issues[0]
    assert scaffold_issue.source == "file_artifact_scanner"
    assert scaffold_issue.path == "src/client/scaffold.ts"
    metadata = dict(scaffold_issue.metadata or {})
    assert metadata["marker_kind"] == "deterministic_scaffold"
    assert metadata["marker_value"] == "structural build passed"
    raw = str((scaffold_issue.metadata or {}).get("raw") or "")
    assert "deterministic scaffold marker" in raw, "metadata.raw must include legacy error string"


def test_scan_detects_generic_payload_store_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "state" / "store.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export interface CardRecord {
  payload: string;
  index: number;
}

export class CardStore {
  private readonly items = new Map<string, CardRecord>();
}

export function cardHelper1(value: number): number { return value + 1; }
export function cardHelper2(value: number): number { return value + 2; }
export function cardHelper3(value: number): number { return value + 3; }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/state/store.ts"])

    assert errors
    assert any("generic payload/index store scaffold" in error for error in errors)


def test_generic_payload_store_scaffold_threads_direct_typed_issue(tmp_path: Path) -> None:
    target = tmp_path / "src" / "state" / "store.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export interface CardRecord {
  payload: string;
  index: number;
}

export class CardStore {
  private readonly items = new Map<string, CardRecord>();
}

export function cardHelper1(value: number): number { return value + 1; }
export function cardHelper2(value: number): number { return value + 2; }
export function cardHelper3(value: number): number { return value + 3; }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["src/state/store.ts"],
    )

    scaffold_issues = [issue for issue in evidence.issues if issue.code == "generic_payload_index_store_scaffold"]
    assert scaffold_issues, "file-level scanner must emit typed generic payload store issue"
    scaffold_issue = scaffold_issues[0]
    assert scaffold_issue.source == "file_artifact_scanner"
    assert scaffold_issue.path == "src/state/store.ts"
    assert scaffold_issue.metadata["helper_count"] == 3
    assert scaffold_issue.metadata["scaffold_kind"] == "generic_payload_index_store"
    raw = str(scaffold_issue.metadata.get("raw") or "")
    assert "generic payload/index store scaffold" in raw


def test_repeated_trivial_arithmetic_tests_thread_direct_typed_issue(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts", count=4)

    evidence = scan_workspace_artifact_quality_evidence(
        str(tmp_path),
        relative_paths=["tests/unit/card-rules.test.ts"],
    )

    trivial_issues = [issue for issue in evidence.issues if issue.code == "repeated_trivial_arithmetic_tests"]
    assert trivial_issues, "file-level scanner must emit typed trivial arithmetic issue"
    trivial_issue = trivial_issues[0]
    assert trivial_issue.source == "file_artifact_scanner"
    assert trivial_issue.path == "tests/unit/card-rules.test.ts"
    assert trivial_issue.metadata["assertion_count"] == 4
    raw = str(trivial_issue.metadata.get("raw") or "")
    assert "repeated trivial arithmetic placeholder tests" in raw


class TestSourceSyntaxInQualityScan:
    """L2-10 r5 regression: `gfm: true;` (a `;` for `,` in an object literal)
    survived the turn because the write-time diagnostic was advisory and the
    artifact-quality scan never re-checked syntax. A syntax-broken artifact is
    now a materialization quality error and enters the repair ladder."""

    def test_js_syntax_error_is_quality_error(self, tmp_path) -> None:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "app.js").write_text(
            "marked.setOptions({\n  breaks: true,\n  gfm: true;\n});\n",
            encoding="utf-8",
        )
        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["app.js"])
        assert any("syntax error in app.js" in e for e in errors), errors

    def test_clean_js_passes(self, tmp_path) -> None:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "app.js").write_text(
            "const editor = document.getElementById('editor');\n"
            "editor.addEventListener('input', () => console.log(editor.value));\n",
            encoding="utf-8",
        )
        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["app.js"])
        assert not any("syntax error" in e for e in errors), errors

    def test_broken_python_is_quality_error(self, tmp_path) -> None:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "main.py").write_text("def add(a, b:\n    return a + b\n", encoding="utf-8")
        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["main.py"])
        assert any("syntax error in main.py" in e for e in errors), errors

    def test_broken_package_manifest_is_quality_error(self, tmp_path) -> None:
        """Only package.json among JSON files enters the scan (data .json files
        are intentionally out of scope; the write-time A5 check covers them)."""
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "package.json").write_text('{"name": "app",}\n', encoding="utf-8")
        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])
        assert any("syntax error in package.json" in e for e in errors), errors

    def test_package_node_eval_syntax_error_is_quality_error(self, tmp_path) -> None:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "package.json").write_text(
            '{"name":"app","scripts":{"test":"node -e \\"const ok = ;\\""}}\n',
            encoding="utf-8",
        )

        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["package.json"])

        assert any("script 'test' has invalid node eval syntax" in e for e in errors), errors

    def test_check_source_file_syntax_unknown_extension_none(self, tmp_path) -> None:
        from polaris.kernelone.quality import check_source_file_syntax

        target = tmp_path / "notes.md"
        target.write_text("# notes\n", encoding="utf-8")
        assert check_source_file_syntax(str(target)) is None


class TestHtmlCompleteness:
    """L2-11 r4: an output-budget-truncated HTML (no closing tags) sailed
    through every gate; truncation signature is now a syntax-class defect."""

    def test_truncated_html_flagged(self, tmp_path) -> None:
        from polaris.kernelone.quality import check_source_file_syntax

        target = tmp_path / "app.html"
        target.write_text("<html><body><script>\nvar diff = [];\n", encoding="utf-8")
        result = check_source_file_syntax(str(target))
        assert result is not None and result["ok"] is False
        assert "missing </html>" in result["error"]
        assert "unclosed <script>" in result["error"]

    def test_complete_html_passes(self, tmp_path) -> None:
        from polaris.kernelone.quality import check_source_file_syntax

        target = tmp_path / "ok.html"
        target.write_text(
            "<html><body><script>var x=1;</script></body></html>\n",
            encoding="utf-8",
        )
        assert check_source_file_syntax(str(target)) == {"ok": True}

    def test_fragment_without_html_tag_passes(self, tmp_path) -> None:
        from polaris.kernelone.quality import check_source_file_syntax

        target = tmp_path / "fragment.html"
        target.write_text("<div>partial include</div>\n", encoding="utf-8")
        assert check_source_file_syntax(str(target)) == {"ok": True}

    def test_truncated_html_enters_quality_scan(self, tmp_path) -> None:
        from polaris.kernelone.quality import scan_workspace_artifact_quality

        (tmp_path / "index.html").write_text("<html><script>\nlet a=1;\n", encoding="utf-8")
        errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["index.html"])
        assert any("syntax error in index.html" in e and "truncated" in e for e in errors), errors


class TestWorkspaceCheckCli:
    def test_fails_on_defective_workspace(self, tmp_path) -> None:
        from polaris.kernelone.quality.workspace_check import main, run_workspace_check

        (tmp_path / "typing_test.html").write_text("<html><script>\nvar x=1;\n", encoding="utf-8")
        checked, failures = run_workspace_check(str(tmp_path))
        assert checked == 1
        assert failures and "typing_test.html" in failures[0]
        assert main(["--workspace", str(tmp_path)]) == 1

    def test_passes_on_clean_workspace(self, tmp_path) -> None:
        from polaris.kernelone.quality.workspace_check import main

        (tmp_path / "index.html").write_text(
            "<html><body><script>console.log('ok');</script></body></html>\n",
            encoding="utf-8",
        )
        (tmp_path / "app.js").write_text("console.log('ok');\n", encoding="utf-8")
        assert main(["--workspace", str(tmp_path)]) == 0

    def test_empty_workspace_passes_vacuously_but_honestly(self, tmp_path) -> None:
        from polaris.kernelone.quality.workspace_check import run_workspace_check

        checked, failures = run_workspace_check(str(tmp_path))
        assert checked == 0
        assert failures == []


def _symbol_errors(errors: list[str]) -> list[str]:
    return [e for e in errors if "unresolved import symbol" in e]


class TestPythonCrossFileSymbolCoherence:
    """L3-16 regression: a package file importing a symbol a sibling never defines
    (SRS_ROTATION_STATES) must fail the quality gate so the Director self-heals,
    instead of shipping a package whose ``import`` raises ImportError at runtime."""

    def test_unresolved_import_symbol_is_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "tetris").mkdir()
        (tmp_path / "tetris" / "constants.py").write_text("BOARD_WIDTH = 10\nBOARD_HEIGHT = 20\n", encoding="utf-8")
        (tmp_path / "tetris" / "__init__.py").write_text(
            "from tetris.constants import BOARD_WIDTH, SRS_ROTATION_STATES\n", encoding="utf-8"
        )

        errors = _symbol_errors(scan_workspace_artifact_quality(str(tmp_path)))

        assert errors, "drift symbol must be flagged"
        assert any("SRS_ROTATION_STATES" in e for e in errors)
        assert not any("BOARD_WIDTH" in e for e in errors), "defined symbol must not be flagged"

    def test_unresolved_import_symbol_has_typed_cross_artifact_issue(self, tmp_path: Path) -> None:
        (tmp_path / "tetris").mkdir()
        (tmp_path / "tetris" / "constants.py").write_text("BOARD_WIDTH = 10\n", encoding="utf-8")
        (tmp_path / "tetris" / "__init__.py").write_text(
            "from .constants import SRS_ROTATION_STATES\n", encoding="utf-8"
        )

        evidence = scan_workspace_artifact_quality_evidence(str(tmp_path))
        issues = [issue.to_dict() for issue in evidence.issues]

        issue = next(item for item in issues if item["code"] == "unresolved_import_symbol")
        assert issue["source"] == "cross_artifact_consistency"
        assert issue["path"] == "tetris/__init__.py"
        assert issue["metadata"]["symbol"] == "SRS_ROTATION_STATES"
        assert issue["metadata"]["importer_path"] == "tetris/__init__.py"
        assert issue["metadata"]["owner_path"] == "tetris/constants.py"
        assert issue["metadata"]["raw"] in evidence.errors

    def test_relative_import_unresolved_symbol_is_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "tetris").mkdir()
        (tmp_path / "tetris" / "constants.py").write_text("BOARD_WIDTH = 10\n", encoding="utf-8")
        (tmp_path / "tetris" / "__init__.py").write_text(
            "from .constants import SRS_ROTATION_STATES\n", encoding="utf-8"
        )

        errors = _symbol_errors(scan_workspace_artifact_quality(str(tmp_path)))

        assert any("SRS_ROTATION_STATES" in e for e in errors)

    def test_resolved_symbols_pass(self, tmp_path: Path) -> None:
        (tmp_path / "tetris").mkdir()
        (tmp_path / "tetris" / "constants.py").write_text(
            "BOARD_WIDTH = 10\nSRS_ROTATION_STATES = {}\n\n\ndef helper():\n    return 1\n", encoding="utf-8"
        )
        (tmp_path / "tetris" / "__init__.py").write_text(
            "from .constants import BOARD_WIDTH, SRS_ROTATION_STATES, helper\n", encoding="utf-8"
        )

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_class_and_function_exports_recognized(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "board.py").write_text(
            "class Board:\n    pass\n\n\ndef make():\n    return Board()\n", encoding="utf-8"
        )
        (tmp_path / "pkg" / "__init__.py").write_text("from .board import Board, make\n", encoding="utf-8")

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_submodule_import_passes(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "board.py").write_text("VALUE = 1\n", encoding="utf-8")
        (tmp_path / "pkg" / "__init__.py").write_text("from . import board\n", encoding="utf-8")

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_external_import_not_flagged(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text(
            "import os\nfrom collections import OrderedDict\nfrom os import path\n\nprint(os, OrderedDict, path)\n",
            encoding="utf-8",
        )

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_wildcard_target_fails_open(self, tmp_path: Path) -> None:
        # constants re-exports via wildcard → its surface is unknown → must NOT flag.
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "base.py").write_text("ANYTHING = 1\n", encoding="utf-8")
        (tmp_path / "pkg" / "constants.py").write_text("from pkg.base import *\n", encoding="utf-8")
        (tmp_path / "pkg" / "__init__.py").write_text("from .constants import SOMETHING_DYNAMIC\n", encoding="utf-8")

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_dunder_getattr_fails_open(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "constants.py").write_text("def __getattr__(name):\n    return name\n", encoding="utf-8")
        (tmp_path / "pkg" / "__init__.py").write_text("from .constants import WHATEVER\n", encoding="utf-8")

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []

    def test_reexported_symbol_recognized(self, tmp_path: Path) -> None:
        # constants re-exports Foo from base via an explicit ImportFrom → Foo IS part of its surface.
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "base.py").write_text("class Foo:\n    pass\n", encoding="utf-8")
        (tmp_path / "pkg" / "constants.py").write_text("from pkg.base import Foo\n", encoding="utf-8")
        (tmp_path / "pkg" / "__init__.py").write_text("from .constants import Foo\n", encoding="utf-8")

        assert _symbol_errors(scan_workspace_artifact_quality(str(tmp_path))) == []


class TestTypescriptCrossFileSymbolCoherence:
    """TS/JS symbol coherence with an emergency opt-out flag.

    Mirrors TestPythonCrossFileSymbolCoherence but for TS/JS named imports. The
    gate runs on every materialization including L2 JS projects, so the matrix is
    dominated by FALSE-POSITIVE guards: every ambiguous/unknowable construct must
    yield ZERO symbol errors. False negatives are acceptable; false positives
    would break the L2 floor.
    """

    _FLAG = "KERNELONE_TS_SYMBOL_COHERENCE"

    def _on(self, monkeypatch) -> None:
        monkeypatch.setenv(self._FLAG, "1")

    def _errors(self, tmp_path: Path) -> list[str]:
        return _symbol_errors(scan_workspace_artifact_quality(str(tmp_path)))

    # --- floor-safety: explicit kill switch remains available ------------------
    def test_explicit_flag_off_is_inert_even_with_missing_symbol(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv(self._FLAG, "0")
        (tmp_path / "sibling.ts").write_text("export const Other = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Missing } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_default_on_flags_missing_named_symbol(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv(self._FLAG, raising=False)
        (tmp_path / "sibling.ts").write_text("export const Other = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Missing } from './sibling';\n", encoding="utf-8")
        assert any("Missing" in e for e in self._errors(tmp_path))

    # --- positive detection ----------------------------------------------------
    def test_missing_named_symbol_flagged(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export const Other = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Missing } from './sibling';\n", encoding="utf-8")
        errors = self._errors(tmp_path)
        assert any("Missing" in e for e in errors)
        assert not any("Other" in e for e in errors)

    def test_missing_named_symbol_flagged_in_js(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.js").write_text("export function helper() {}\n", encoding="utf-8")
        (tmp_path / "main.js").write_text("import { ghost } from './sibling';\n", encoding="utf-8")
        assert any("ghost" in e for e in self._errors(tmp_path))

    def test_aliased_import_checks_original_name(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export const Present = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Missing as M } from './sibling';\n", encoding="utf-8")
        errors = self._errors(tmp_path)
        assert any("Missing" in e for e in errors)

    def test_named_import_clause_comments_are_not_symbols(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.js").write_text("export const Present = 1;\n", encoding="utf-8")
        (tmp_path / "main.js").write_text(
            "import {\n  Present, // local facade note\n} from './sibling';\n",
            encoding="utf-8",
        )

        assert self._errors(tmp_path) == []

    # --- export-form recognition (must NOT flag) -------------------------------
    def test_all_declaration_export_forms_recognized(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text(
            "export const A = 1;\n"
            "export function B() {}\n"
            "export class C {}\n"
            "export interface D { x: number }\n"
            "export type E = string;\n"
            "export enum F { X }\n"
            "export async function G() {}\n"
            "export abstract class H {}\n",
            encoding="utf-8",
        )
        (tmp_path / "index.ts").write_text("import { A, B, C, D, E, F, G, H } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_aliased_export_recognized(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("const x = 1;\nexport { x as Public };\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Public } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_reexport_clause_recognized(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "base.ts").write_text("export const Z = 1;\n", encoding="utf-8")
        (tmp_path / "sibling.ts").write_text("export { Z } from './base';\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Z } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    # --- fail-open guards (unknowable surface -> ZERO symbol errors) -----------
    def test_export_star_barrel_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export * from './deep';\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Anything } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_commonjs_module_exports_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.js").write_text("module.exports = { foo: 1 };\n", encoding="utf-8")
        (tmp_path / "main.js").write_text("import { bar } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_commonjs_exports_property_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.js").write_text("exports.foo = 1;\n", encoding="utf-8")
        (tmp_path / "main.js").write_text("import { bar } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_export_assignment_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("class Thing {}\nexport = Thing;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Whatever } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_destructured_export_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text(
            "const o = { a: 1, b: 2 };\nexport const { a, b } = o;\n", encoding="utf-8"
        )
        (tmp_path / "index.ts").write_text("import { missing } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_declare_module_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("declare module 'x' { export const Y: number }\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Missing } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    # --- import shapes that must NOT be checked --------------------------------
    def test_default_import_not_checked(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export default function () {}\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import Whatever from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_namespace_import_not_checked(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export const A = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import * as NS from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_type_only_import_not_checked(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export const A = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import type { Missing } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_inline_type_member_skipped(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export const Real = 1;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { type Phantom, Real } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_default_plus_named_checks_only_named(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("export default 1;\nexport const Named = 2;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import Def, { Named } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []

    def test_unresolved_specifier_yields_no_symbol_error(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "index.ts").write_text("import { X } from './does-not-exist';\n", encoding="utf-8")
        # The module-missing case is reported by the existing relative-import
        # check, not as a symbol error; the resolver returns None -> skipped.
        assert self._errors(tmp_path) == []

    def test_commented_export_does_not_cause_false_positive(self, tmp_path: Path, monkeypatch) -> None:
        self._on(monkeypatch)
        (tmp_path / "sibling.ts").write_text("// export const Hidden = 1;\nexport const Shown = 2;\n", encoding="utf-8")
        (tmp_path / "index.ts").write_text("import { Shown } from './sibling';\n", encoding="utf-8")
        assert self._errors(tmp_path) == []
