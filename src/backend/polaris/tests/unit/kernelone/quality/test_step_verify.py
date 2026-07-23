"""KernelOne step-verify toolkit (consolidated from three cell mirrors).

Live I3-r12: a step passed 7/8 verify clauses but died because teaching never
said WHICH clause failed nor BY HOW MUCH (the file needed 38 lines removed).
The toolkit owns clause diagnosis + T2 measured-vs-required residuals for the
three verify touchpoints (in-turn self-check, QA acceptance, punch list).
"""

from __future__ import annotations

import json
from pathlib import Path

from polaris.kernelone.quality.step_verify import (
    StepVerifyCommandSafetyAssessment,
    assess_legacy_step_verify_command_safety,
    assess_step_verify_command_safety,
    clause_residual,
    collect_failing_clauses,
    first_failing_verify_clause,
    normalize_step_verify,
    run_step_verify,
    split_verify_clauses,
    split_verify_directed_effect_commands,
    verify_has_structural_clause,
    verify_is_all_hollow,
)


class TestStepVerifyCommandSafety:
    def test_allows_python_py_compile(self) -> None:
        assessment = assess_step_verify_command_safety("python -m py_compile app.py")

        assert isinstance(assessment, StepVerifyCommandSafetyAssessment)
        assert assessment.allowed is True
        assert assessment.reason == "all_clauses_allowed"
        assert assessment.blocked_tokens == ()
        assert assessment.clauses == ("python -m py_compile app.py",)

    def test_allows_pytest(self) -> None:
        assert assess_step_verify_command_safety("pytest -q").allowed is True

    def test_allows_go_test(self) -> None:
        assert assess_step_verify_command_safety("go test ./...").allowed is True

    def test_allows_test_and_grep_chain(self) -> None:
        command = "test -f src/app.py && grep -q 'class App' src/app.py"
        assessment = assess_step_verify_command_safety(command)

        assert assessment.allowed is True
        assert assessment.clauses == ("test -f src/app.py", "grep -q 'class App' src/app.py")

    def test_denies_rm(self) -> None:
        assessment = assess_step_verify_command_safety("rm -rf .")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_command:rm"
        assert assessment.blocked_tokens == ("rm",)
        assert assessment.blocked_clauses == ("rm -rf .",)

    def test_denies_python_c(self) -> None:
        assessment = assess_step_verify_command_safety("python -c 'print(1)'")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_dynamic_execution:python -c"
        assert assessment.blocked_tokens == ("python -c",)

    def test_denies_curl(self) -> None:
        assessment = assess_step_verify_command_safety("curl http://example.test")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_command:curl"
        assert assessment.blocked_tokens == ("curl",)

    def test_denies_semicolon_chain(self) -> None:
        assessment = assess_step_verify_command_safety("pytest -q; rm -rf .")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_shell_token:;"
        assert ";" in assessment.blocked_tokens

    def test_denies_pipe_to_shell(self) -> None:
        assessment = assess_step_verify_command_safety("cat file | sh")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_shell_token:|"
        assert "|" in assessment.blocked_tokens

    def test_denies_output_redirect(self) -> None:
        assessment = assess_step_verify_command_safety("npm test > out.txt")

        assert assessment.allowed is False
        assert assessment.reason == "blocked_shell_token:>"
        assert ">" in assessment.blocked_tokens

    def test_denies_empty_command(self) -> None:
        assessment = assess_step_verify_command_safety("")

        assert assessment.allowed is False
        assert assessment.reason == "empty_command"
        assert assessment.clauses == ()


class TestLegacyStepVerifyCommandSafety:
    def test_allows_legacy_wc_line_count_substitution_that_strict_rejects(self) -> None:
        command = 'test -f ./style.css && grep -q "#game" ./style.css && [ "$(wc -l < ./style.css)" -le 120 ]'

        strict = assess_step_verify_command_safety(command)
        legacy = assess_legacy_step_verify_command_safety(command)

        assert strict.allowed is False
        assert legacy.allowed is True
        assert legacy.reason == "legacy_shell_verify_allowed"

    def test_allows_legacy_cd_and_or_chains_that_strict_rejects(self) -> None:
        for command in (
            "cd src && test -f app.js && grep -q foo app.js",
            "test -f ./a.txt && grep -q x ./a.txt || test -f ./b.txt",
        ):
            strict = assess_step_verify_command_safety(command)
            legacy = assess_legacy_step_verify_command_safety(command)

            assert strict.allowed is False
            assert legacy.allowed is True

    def test_denies_legacy_destructive_commands(self) -> None:
        for command, reason in (
            ("rm -rf .", "blocked_command:rm"),
            ("mv a b", "blocked_command:mv"),
            ("cp a b", "blocked_command:cp"),
            ("chmod 777 app.py", "blocked_command:chmod"),
            ("chown user app.py", "blocked_command:chown"),
            ("sudo test -f app.py", "blocked_command:sudo"),
            ("curl http://example.test", "blocked_command:curl"),
            ("wget http://example.test", "blocked_command:wget"),
            ("nc localhost 80", "blocked_command:nc"),
            ("netcat localhost 80", "blocked_command:netcat"),
            ("ssh host", "blocked_command:ssh"),
            ("scp a host:b", "blocked_command:scp"),
            ("kill 123", "blocked_command:kill"),
            ("pkill node", "blocked_command:pkill"),
            ("killall node", "blocked_command:killall"),
        ):
            assessment = assess_legacy_step_verify_command_safety(command)

            assert assessment.allowed is False, command
            assert assessment.reason == reason

    def test_denies_legacy_dynamic_execution(self) -> None:
        for command, reason in (
            ("bash -c 'echo ok'", "blocked_dynamic_execution:bash -c"),
            ("sh -c 'echo ok'", "blocked_dynamic_execution:sh -c"),
            ("python -c 'print(1)'", "blocked_dynamic_execution:python -c"),
            ("node -e 'console.log(1)'", "blocked_dynamic_execution:node -e"),
        ):
            assessment = assess_legacy_step_verify_command_safety(command)

            assert assessment.allowed is False, command
            assert assessment.reason == reason

    def test_denies_legacy_shell_control_tokens(self) -> None:
        for command, token in (
            ("test -f app.py; test -f other.py", ";"),
            ("cat app.py | grep ok", "|"),
            ("sleep 1 & test -f app.py", "&"),
            ("test -f `pwd`", "`"),
            ("test -f $(pwd)", "$()"),
        ):
            assessment = assess_legacy_step_verify_command_safety(command)

            assert assessment.allowed is False, command
            assert assessment.reason == f"blocked_shell_token:{token}"


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
        assert normalize_step_verify(["test -f a.md", " grep -q x a.md "]) == "test -f a.md && grep -Fq x a.md"

    def test_empty_shapes(self) -> None:
        assert normalize_step_verify(None) == ""
        assert normalize_step_verify([]) == ""
        assert normalize_step_verify(["", "  "]) == ""

    def test_bash_here_string_rewrites_to_posix_pipe(self) -> None:
        verify = 'python calculator.py <<< "1"'

        assert normalize_step_verify(verify) == "printf '%s\\n' 1 | python calculator.py"

    def test_bash_here_string_rewrites_inside_and_chain(self) -> None:
        verify = 'python -c "print(1)" && python calculator.py <<< "2+3"'

        assert normalize_step_verify(verify) == "python -c \"print(1)\" && printf '%s\\n' 2+3 | python calculator.py"

    def test_bash_here_string_rewrites_before_pipeline_suffix(self) -> None:
        verify = "python calculator.py <<< 'exit' | head -n 5"

        assert normalize_step_verify(verify) == "printf '%s\\n' exit | python calculator.py | head -n 5"

    def test_quoted_here_string_marker_is_preserved(self) -> None:
        verify = "python -c \"print('<<<')\""

        assert normalize_step_verify(verify) == verify

    def test_normalized_here_string_runs_under_bin_sh(self, tmp_path: Path) -> None:
        (tmp_path / "read_stdin.py").write_text(
            "import sys\nassert sys.stdin.readline().strip() == 'ok'\n",
            encoding="utf-8",
        )
        verify = normalize_step_verify("python3 read_stdin.py <<< 'ok'")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 0

    def test_normalized_here_string_pipeline_runs_under_bin_sh(self, tmp_path: Path) -> None:
        (tmp_path / "read_stdin.py").write_text(
            "import sys\nprint(sys.stdin.readline().strip())\n",
            encoding="utf-8",
        )
        verify = normalize_step_verify("python3 read_stdin.py <<< 'ok' | grep -q ok")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 0

    def test_verify_script_self_recursion_fails_fast(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "verify.js").write_text(
            "const { execSync } = require('child_process');\n"
            "execSync('node scripts/verify.js');\n"
            "console.log('PASS');\n",
            encoding="utf-8",
        )

        outcome = run_step_verify("node scripts/verify.js", cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 1
        assert "recursively invokes itself" in outcome[1]

    def test_simple_grep_q_is_normalized_to_literal_grep(self) -> None:
        verify = "grep -q '3 + (4 - 2) * 5' README.md"

        assert normalize_step_verify(verify) == "grep -Fq '3 + (4 - 2) * 5' README.md"

    def test_html_open_tag_grep_allows_attributes(self, tmp_path: Path) -> None:
        (tmp_path / "index.html").write_text('<!doctype html>\n<html lang="en">\n</html>\n', encoding="utf-8")
        verify = normalize_step_verify("grep -q '<html>' index.html")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert verify == "grep -Fiq '<html' index.html"
        assert outcome is not None
        assert outcome[0] == 0

    def test_dist_require_verify_runs_package_build_first(self, tmp_path: Path) -> None:
        package = {
            "scripts": {
                "build": (
                    "node -e \"const fs=require('fs');"
                    "fs.mkdirSync('dist/models',{recursive:true});"
                    "fs.writeFileSync('dist/models/moonphase.js', "
                    '\\"exports.MoonPhase = class MoonPhase { getPhase(){ return \'full\'; } };\\");"'
                )
            }
        }
        (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
        verify = "node -e \"const {MoonPhase}=require('./dist/models/moonphase.js'); console.log(new MoonPhase().getPhase())\""
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 0
        assert "full" in outcome[1]

    def test_explicit_regex_grep_is_preserved(self) -> None:
        verify = "grep -Eq 'class .+App' src/main.js"

        assert normalize_step_verify(verify) == verify

    def test_regex_like_grep_is_not_forced_to_literal(self) -> None:
        verify = "grep -q '[0-9]' README.md"

        assert normalize_step_verify(verify) == "grep -Eq '[0-9]' README.md"

    def test_misflagged_fixed_grep_digit_class_is_normalized_to_regex(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "Example: 3 + 4 = 7\n",
            encoding="utf-8",
        )
        verify = normalize_step_verify("grep -Fq '[0-9]' README.md")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert verify == "grep -Eq '[0-9]' README.md"
        assert outcome is not None
        assert outcome[0] == 0

    def test_literal_bracket_text_stays_fixed_grep(self) -> None:
        verify = "grep -Fq '[TODO]' README.md"

        assert normalize_step_verify(verify) == verify

    def test_normalized_literal_grep_runs_under_bin_sh(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            'python calculator.py "3 + (4 - 2) * 5"\n',
            encoding="utf-8",
        )
        verify = normalize_step_verify("grep -q '3 + (4 - 2) * 5' README.md")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 0

    def test_basic_grep_or_pattern_is_normalized_to_literal_alternates(self) -> None:
        verify = r"grep -q 'exit\|quit' README.md"

        assert normalize_step_verify(verify) == "grep -Fq -e exit -e quit README.md"

    def test_fixed_grep_or_pattern_is_normalized_to_literal_alternates(self) -> None:
        verify = r"grep -Fq 'exit\|quit' README.md"

        assert normalize_step_verify(verify) == "grep -Fq -e exit -e quit README.md"

    def test_normalized_grep_or_pattern_runs_under_bin_sh(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text(
            "Run python calculator.py, then type exit or quit to leave.\n",
            encoding="utf-8",
        )
        verify = normalize_step_verify(r"grep -Fq 'exit\|quit' README.md")
        outcome = run_step_verify(verify, cwd=str(tmp_path))

        assert outcome is not None
        assert outcome[0] == 0


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

    def test_quoted_and_is_preserved_during_diagnosis(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("plain\n", encoding="utf-8")
        verify = "grep -q 'a && b' ./a.txt && test -f ./a.txt"
        detail = first_failing_verify_clause(verify, cwd=str(tmp_path))
        assert detail.startswith("failing clause [1/2]: grep -q 'a && b' ./a.txt")


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


def test_split_clauses_preserves_quoted_and_nested_operators() -> None:
    verify = "grep -q 'a && b' ./a.txt && test -f \"$(printf 'x && y')\""

    assert split_verify_clauses(verify) == [
        "grep -q 'a && b' ./a.txt",
        "test -f \"$(printf 'x && y')\"",
    ]


def test_directed_effect_commands_preserve_shared_shell_state_and_or() -> None:
    for verify in (
        "cd src && test -f app.py",
        "X=1 && [ \"$X\" = 1 ]",
        "test -f a || test -f b",
    ):
        assert split_verify_directed_effect_commands(verify) == [verify]


def test_directed_effect_commands_split_independent_and_clauses() -> None:
    assert split_verify_directed_effect_commands("test -f a && grep -q x a") == [
        "test -f a",
        "grep -q x a",
    ]


class TestNormalizeStepVerify:
    def test_strips_trailing_natural_language_from_pytest_verify(self) -> None:
        verify = "pytest -k test_create_app 通过，验证 Flask app 创建成功"

        assert normalize_step_verify(verify) == "pytest -k test_create_app"

    def test_preserves_quoted_natural_language_inside_verify_command(self) -> None:
        verify = "python -c \"print('通过，验证')\""

        assert normalize_step_verify(verify) == verify
