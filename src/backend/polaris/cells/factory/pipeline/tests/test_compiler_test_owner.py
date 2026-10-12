"""Compiler locations in tests outrank imported production read context."""

from pathlib import Path

import pytest
from polaris.cells.director.runtime.public import QueryDirectorRepairCoverageV1, query_director_repair_coverage
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_impl import (
    _workspace_quality_causal_repair_target_files,
    _workspace_quality_llm_claim_target_files,
)


@pytest.mark.parametrize("path", ["tests/rules.test.ts", "/workspace/tests/rules.test.ts"])
def test_typescript_compiler_test_location_keeps_exact_owner(tmp_path: Path, path: str) -> None:
    source = tmp_path / "src/value.ts"
    source.parent.mkdir()
    source.write_text("export const value = 7;\n", encoding="utf-8")
    test = tmp_path / "tests/rules.test.ts"
    test.parent.mkdir()
    test.write_text("import { value } from '../src/value';\nconst validate = (err) => value;\n", encoding="utf-8")
    error = f"{path}(2,19): error TS7006: Parameter 'err' implicitly has an 'any' type."
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(error,)))
    assert coverage.total_diagnostics == 1
    executor = OrchestrationStageExecutor(tmp_path)

    targets = _workspace_quality_causal_repair_target_files(executor, artifact_quality_errors=[error])
    owner = _workspace_quality_llm_claim_target_files(
        owner_target_files=None, diagnostic_target_files=targets, fallback_target_files=[]
    )

    assert targets == ["tests/rules.test.ts"]
    assert owner == ["tests/rules.test.ts"]
    assert source.read_text(encoding="utf-8") == "export const value = 7;\n"
