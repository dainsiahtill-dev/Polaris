"""KernelOne step-verify toolkit (consolidated from three cell mirrors).

Live I3-r12: a step passed 7/8 verify clauses but died because teaching never
said WHICH clause failed nor BY HOW MUCH (the file needed 38 lines removed).
The toolkit owns clause diagnosis + T2 measured-vs-required residuals for the
three verify touchpoints (in-turn self-check, QA acceptance, punch list).
"""

from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality.step_verify import (
    clause_residual,
    collect_failing_clauses,
    first_failing_verify_clause,
    normalize_step_verify,
    split_verify_clauses,
    verify_has_structural_clause,
    verify_is_all_hollow,
)


class TestHollowVerifyClassifier:
    """I3-r21: an existence-only verify can 'resolve' a code step on a stub."""

    def test_test_f_plus_filename_grep_is_all_hollow(self) -> None:
        assert verify_is_all_hollow("test -f main.js && grep -q 'main.js' main.js", signature_tokens=set())

    def test_node_check_is_structural(self) -> None:
        assert not verify_is_all_hollow("test -f main.js && node --check main.js", signature_tokens=set())
        assert verify_has_structural_clause("test -f main.js && node --check main.js", signature_tokens=set())

    def test_grep_for_declared_signature_is_structural(self) -> None:
        assert not verify_is_all_hollow(
            "test -f main.js && grep -q 'class Paddle' main.js",
            signature_tokens={"class Paddle extends Sprite"},
        )

    def test_grep_for_unrelated_marker_stays_hollow(self) -> None:
        assert verify_is_all_hollow(
            "test -f main.js && grep -q 'main.js' main.js",
            signature_tokens={"class Paddle"},
        )

    def test_wc_line_count_is_hollow(self) -> None:
        assert verify_is_all_hollow('test -f main.js && [ "$(wc -l < main.js)" -ge 5 ]', signature_tokens=set())

    def test_empty_verify_is_not_hollow(self) -> None:
        # empty verify is handled separately as "missing verify", not "hollow"
        assert not verify_is_all_hollow("", signature_tokens=set())

    def test_unparseable_clause_fails_open_as_structural(self) -> None:
        assert not verify_is_all_hollow("python -m pytest tests/ -k smoke", signature_tokens=set())


class TestNormalize:
    def test_string_passthrough(self) -> None:
        assert normalize_step_verify("  test -f a.md ") == "test -f a.md"

    def test_array_joined(self) -> None:
        assert normalize_step_verify(["test -f a.md", " grep -q x a.md "]) == "test -f a.md && grep -q x a.md"

    def test_empty_shapes(self) -> None:
        assert normalize_step_verify(None) == ""
        assert normalize_step_verify([]) == ""
        assert normalize_step_verify(["", "  "]) == ""


class TestClauseResidual:
    def test_wc_le_overage_reports_lines_to_delete(self, tmp_path: Path) -> None:
        (tmp_path / "style.css").write_text("x\n" * 158, encoding="utf-8")
        residual = clause_residual('[ "$(wc -l < ./style.css)" -le 120 ]', cwd=str(tmp_path))
        assert "实测 158 行" in residual
        assert "≤120" in residual
        assert "需删 38 行" in residual

    def test_wc_ge_shortfall_reports_lines_to_add(self, tmp_path: Path) -> None:
        (tmp_path / "readme.md").write_text("a\nb\n", encoding="utf-8")
        residual = clause_residual('[ "$(wc -l < ./readme.md)" -ge 10 ]', cwd=str(tmp_path))
        assert "实测 2 行" in residual
        assert "需增 8 行" in residual

    def test_wc_missing_file(self, tmp_path: Path) -> None:
        residual = clause_residual('[ "$(wc -l < ./absent.css)" -le 120 ]', cwd=str(tmp_path))
        assert residual == "文件不存在"

    def test_test_f_missing_file(self, tmp_path: Path) -> None:
        assert clause_residual("test -f ./readme.md", cwd=str(tmp_path)) == "文件不存在, 需创建"

    def test_test_f_existing_file_silent(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x", encoding="utf-8")
        assert clause_residual("test -f ./a.md", cwd=str(tmp_path)) == ""

    def test_grep_on_missing_file_names_the_real_cause(self, tmp_path: Path) -> None:
        residual = clause_residual("grep -q 'const LEVELS' ./main.js", cwd=str(tmp_path))
        assert "main.js 不存在" in residual

    def test_grep_on_existing_file_silent(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("var x\n", encoding="utf-8")
        assert clause_residual("grep -q 'const LEVELS' ./main.js", cwd=str(tmp_path)) == ""

    def test_unparseable_clause_stays_silent(self, tmp_path: Path) -> None:
        assert clause_residual("node --check ./main.js", cwd=str(tmp_path)) == ""


class TestFirstFailingClause:
    def test_names_clause_with_residual(self, tmp_path: Path) -> None:
        (tmp_path / "style.css").write_text("#game {}\n" * 200, encoding="utf-8")
        verify = 'test -f ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'
        detail = first_failing_verify_clause(verify, cwd=str(tmp_path))
        assert detail.startswith("failing clause [2/2]:")
        assert "需删 80 行" in detail

    def test_state_carrying_chain_abandons(self, tmp_path: Path) -> None:
        assert first_failing_verify_clause("cd src && test -f app.js", cwd=str(tmp_path)) == ""

    def test_top_level_or_abandons(self, tmp_path: Path) -> None:
        verify = "test -f ./a && grep -q x ./a || test -f ./b"
        assert first_failing_verify_clause(verify, cwd=str(tmp_path)) == ""

    def test_quoted_and_abandons(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        verify = "grep -q 'a && b' ./a.txt && test -f ./a.txt"
        assert first_failing_verify_clause(verify, cwd=str(tmp_path)) == ""


class TestCollectFailingClauses:
    def test_punch_list_with_residuals(self, tmp_path: Path) -> None:
        (tmp_path / "main.js").write_text("function draw() {}\n", encoding="utf-8")
        verify = "test -f ./main.js && grep -q 'const LEVELS' ./main.js && grep -q 'function draw' ./main.js"
        result = collect_failing_clauses(verify, cwd=str(tmp_path))
        assert result is not None
        assert result["exit_code"] != 0
        assert result["total_clauses"] == 3
        assert result["failing_clauses"] == ["grep -q 'const LEVELS' ./main.js"]

    def test_passing_pre_state(self, tmp_path: Path) -> None:
        (tmp_path / "a.md").write_text("x\n", encoding="utf-8")
        result = collect_failing_clauses("test -f ./a.md", cwd=str(tmp_path))
        assert result == {"exit_code": 0, "failing_clauses": [], "total_clauses": 1}

    def test_diagnosis_abandoned_keeps_whole_verdict(self, tmp_path: Path) -> None:
        result = collect_failing_clauses("cd src && test -f app.js", cwd=str(tmp_path))
        assert result is not None
        assert result["exit_code"] != 0
        assert result["failing_clauses"] == []

    def test_fifteen_clause_step_still_diagnosed(self, tmp_path: Path) -> None:
        # live I3-r14: a single main.js step carried 15 cheap grep obligations and
        # used to lose clause-level teaching at the old 12-clause cap.
        (tmp_path / "main.js").write_text("function draw() {}\n", encoding="utf-8")
        clauses = ["test -f ./main.js"] + [f"grep -q 'sym{i}' ./main.js" for i in range(14)]
        verify = " && ".join(clauses)
        result = collect_failing_clauses(verify, cwd=str(tmp_path))
        assert result is not None
        assert result["total_clauses"] == 15
        assert result["failing_clauses"]  # diagnosis was NOT abandoned


def test_split_clauses() -> None:
    assert split_verify_clauses("a && b &&  c ") == ["a", "b", "c"]


class TestNormalizeStepVerify:
    def test_strips_trailing_natural_language_from_pytest_verify(self) -> None:
        verify = "pytest -k test_create_app 通过，验证 Flask app 创建成功"

        assert normalize_step_verify(verify) == "pytest -k test_create_app"

    def test_preserves_quoted_natural_language_inside_verify_command(self) -> None:
        verify = "python -c \"print('通过，验证')\""

        assert normalize_step_verify(verify) == verify
