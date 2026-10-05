"""Regressions for diagnostic identity and located TypeScript compiler input."""

from __future__ import annotations

import pytest
from polaris.cells.director.runtime.internal.repair_kernel.contracts import RepairDiagnostic
from polaris.cells.director.runtime.public import (
    QueryDirectorRepairCoverageV1,
    normalize_director_repair_diagnostics,
    query_director_repair_coverage,
)


def test_public_normalizer_retains_distinct_relative_import_targets() -> None:
    diagnostics = normalize_director_repair_diagnostics(
        (
            "unresolved relative import '../dist/alpha/index.js' in tests/verify.test.ts",
            "unresolved relative import '../dist/beta/index.js' in tests/verify.test.ts",
        )
    )

    assert len(diagnostics) == 2
    assert {item.metadata["specifier"] for item in diagnostics} == {
        "../dist/alpha/index.js",
        "../dist/beta/index.js",
    }
    assert len({item.metadata["diagnostic_id"] for item in diagnostics}) == 2
    assert all(item.code == "unresolved_relative_import" for item in diagnostics)
    assert all(item.path == "tests/verify.test.ts" for item in diagnostics)


def test_repeated_import_fact_deduplicates_across_raw_and_structured_input() -> None:
    diagnostics = normalize_director_repair_diagnostics(
        (
            "unresolved relative import '../dist/alpha/index.js' in tests/verify.test.ts",
            {
                "source": "artifact_quality",
                "code": "unresolved_relative_import",
                "message": "Relative import target is unresolved.",
                "path": "tests/verify.test.ts",
                "specifier": "../dist/alpha/index.js",
                "raw": "nested verifier reports the same import",
                "metadata": {"telemetry": "nested"},
            },
        )
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].metadata["specifier"] == "../dist/alpha/index.js"


def test_kernel_identity_distinguishes_semantic_specifier() -> None:
    alpha = RepairDiagnostic(
        source="artifact_quality",
        code="unresolved_relative_import",
        message="Relative import target is unresolved.",
        path="tests/verify.test.ts",
        metadata={"specifier": "../dist/alpha/index.js"},
    )
    beta = RepairDiagnostic(
        source=alpha.source,
        code=alpha.code,
        message=alpha.message,
        path=alpha.path,
        metadata={"specifier": "../dist/beta/index.js"},
    )

    assert alpha.diagnostic_id != beta.diagnostic_id


@pytest.mark.parametrize("explicit_id", ["supplied-id", "  supplied-id  "])
def test_public_normalizer_preserves_explicit_identity(explicit_id: str) -> None:
    diagnostics = normalize_director_repair_diagnostics(
        (
            {
                "code": "unresolved_relative_import",
                "message": "Relative import target is unresolved.",
                "path": "tests/verify.test.ts",
                "diagnostic_id": explicit_id,
                "metadata": {"specifier": "../dist/alpha/index.js"},
            },
        )
    )

    assert diagnostics[0].metadata["diagnostic_id"] == "supplied-id"


@pytest.mark.parametrize("metadata", [{}, {"specifier": ""}, {"specifier": "  "}, {"specifier": None}])
def test_absent_semantic_specifier_preserves_legacy_identity(metadata: dict[str, object]) -> None:
    diagnostic = RepairDiagnostic(
        source="artifact_quality",
        code="unresolved_relative_import",
        message="Relative import target is unresolved.",
        path="tests/verify.test.ts",
        metadata=metadata,
    )

    # Existing externally observed identity; no production hash helper in the expectation.
    assert diagnostic.diagnostic_id == "diag_c0a3f3f526b673bf91aa04d1"


def test_irrelevant_metadata_and_raw_evidence_do_not_change_identity() -> None:
    primary = RepairDiagnostic(
        source="artifact_quality",
        code="unresolved_relative_import",
        message="Relative import target is unresolved.",
        path="tests/verify.test.ts",
        raw="direct verifier",
        metadata={"specifier": "../dist/alpha/index.js", "telemetry": 1},
    )
    nested = RepairDiagnostic(
        source=primary.source,
        code=primary.code,
        message=primary.message,
        path=primary.path,
        raw="nested verifier",
        metadata={"trace_id": "different", "telemetry": 2, "specifier": " ../dist/alpha/index.js "},
    )

    assert primary.diagnostic_id == nested.diagnostic_id


@pytest.mark.parametrize("path", ["tsconfig.json", "config/tsconfig.build.json", "src/main.ts", "src/view.tsx"])
def test_located_typescript_errors_retain_code_path_and_location(path: str) -> None:
    message = "Option 'module' must be set to 'NodeNext' when option 'moduleResolution' is set to 'NodeNext'."
    raw = f"{path}(4,15): error TS5110: {message}"
    diagnostics = normalize_director_repair_diagnostics((raw,))

    assert len(diagnostics) == 1
    diagnostic = diagnostics[0]
    assert diagnostic.code == "typescript_ts5110"
    assert diagnostic.path == path
    assert diagnostic.message == message
    assert diagnostic.metadata["line"] == 4
    assert diagnostic.metadata["column"] == 15
    assert diagnostic.metadata["raw"] == raw


def test_nested_json_compiler_fact_deduplicates_and_retains_continuation() -> None:
    raw = "tsconfig.json(4,15): error TS5110: Invalid module configuration."
    continuation = "The module resolution option requires a compatible module option."
    diagnostics = normalize_director_repair_diagnostics(
        (raw, f"  {continuation}", f"workspace validation command failed (tsc):\n{raw}\n  {continuation}")
    )

    assert len(diagnostics) == 1
    assert diagnostics[0].code == "typescript_ts5110"
    assert diagnostics[0].message == f"Invalid module configuration.\n{continuation}"
    assert diagnostics[0].metadata["line"] == 4
    assert diagnostics[0].metadata["column"] == 15


def test_typed_ts5110_remains_an_uncovered_rule_gap() -> None:
    report = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=("tsconfig.json(4,15): error TS5110: Invalid module configuration.",)
        )
    )

    assert report.total_diagnostics == 1
    assert report.uncovered_diagnostic_count == 1
    assert report.executable_runtime_plan_diagnostic_count == 0
    assert report.items[0].diagnostic["code"] == "typescript_ts5110"
    assert report.items[0].diagnostic["path"] == "tsconfig.json"
    assert not report.items[0].known_rule_matched
    assert not report.items[0].executable_runtime_plan_matched
