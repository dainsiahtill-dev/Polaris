from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.code_intelligence.engine.public.contracts import (
    AstDependencyVerificationResultV1,
    VerifyAstDependencyQueryV1,
)
from polaris.cells.code_intelligence.engine.public.service import verify_ast_dependency


def test_verify_ast_dependency_query_normalizes_required_fields() -> None:
    query = VerifyAstDependencyQueryV1(
        workspace=" /repo ",
        path=" app.py ",
        language=" Python ",
        symbol=" handle ",
    )

    assert query.workspace == "/repo"
    assert query.path == "app.py"
    assert query.language == "python"
    assert query.symbol == "handle"


def test_verify_ast_dependency_query_rejects_empty_symbol() -> None:
    with pytest.raises(ValueError, match="symbol must be a non-empty string"):
        VerifyAstDependencyQueryV1(workspace="/repo", path="app.py", language="python", symbol="")


def test_verify_ast_dependency_public_service_uses_typed_result(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("def handle() -> str:\n    return 'ok'\n", encoding="utf-8")

    result = verify_ast_dependency(
        VerifyAstDependencyQueryV1(
            workspace=str(workspace),
            path="app.py",
            language="python",
            symbol="handle",
            kind="function",
            max_results=5,
        )
    )

    assert isinstance(result, AstDependencyVerificationResultV1)
    assert result.ok is True
    assert result.workspace == str(workspace)
    assert result.path == "app.py"
    assert result.symbol == "handle"
    assert result.result_count >= 1
    assert result.results[0]["file"] == "app.py"
