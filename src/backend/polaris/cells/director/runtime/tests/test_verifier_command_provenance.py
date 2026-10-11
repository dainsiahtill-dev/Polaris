"""Public regressions for bounded verifier command provenance, not write authority."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    normalize_director_repair_diagnostics,
    plan_director_repair,
    query_director_repair_coverage,
)

MISSING_TARGET = "deterministic_javascript_test_missing_target_repair"
NPM_SCRIPT = "deterministic_npm_script_contract_repair"


@pytest.mark.parametrize(
    ("command", "output", "codes", "source_tool", "package"),
    [
        (
            "npm test",
            "> sample@0.1.0 test\n> node --test --import tsx ./test\nCould not find './test'",
            ["workspace_validation_failed"],
            MISSING_TARGET,
            '{"main":"dist/main.js","scripts":{"test":"node --test --import tsx ./test"}}',
        ),
        (
            "npm test",
            "> sample@0.1.0 test\n> node --test dist/__tests__\nCould not find 'dist/__tests__'",
            ["workspace_validation_failed"],
            MISSING_TARGET,
            '{"main":"dist/main.js","scripts":{"test":"node --test dist/__tests__"}}',
        ),
        (
            "npm run start",
            "> node --loader ts-node/esm src/main.ts || node -r ts-node/register src/main.ts\n"
            "Error [ERR_REQUIRE_CYCLE_MODULE]: Cannot require() ES Module /tmp/demo/src/main.ts in a cycle.\n"
            "src/main.ts(2,1): error TS6133: 'Unused' is declared but its value is never read.",
            ["typescript_ts6133", "javascript_module_error"],
            NPM_SCRIPT,
            '{"scripts":{"build":"tsc -p tsconfig.json","start":"node --loader ts-node/esm src/main.ts"}}',
        ),
        (
            "npm run start",
            "> sample@0.1.0 start\n> npx --yes http-server . -p 8080 -c-1\n"
            "Error: listen EADDRINUSE: address already in use 0.0.0.0:8080",
            ["workspace_validation_failed"],
            NPM_SCRIPT,
            '{"scripts":{"start":"npx --yes http-server . -p 8080 -c-1"}}',
        ),
    ],
    ids=["missing-test-directory", "missing-dist-tests", "mixed-ts-loader-cycle", "fixed-port"],
)
def test_real_command_remains_with_its_failure_for_public_coverage_and_shadow_plan(
    command: str,
    output: str,
    codes: list[str],
    source_tool: str,
    package: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Removing command provenance must break coverage and typed planning here.
    raw = f"Artifact quality scan failed: workspace validation command failed ({command}):\n{output}"
    errors = [raw]
    base_files = {
        "package.json": package,
        "tsconfig.json": '{"compilerOptions":{"outDir":"dist","rootDir":"src"}}',
        "src/main.ts": "export function main(): void {}\n",
    }
    original = deepcopy((errors, base_files))
    monkeypatch.chdir(tmp_path)

    diagnostics = normalize_director_repair_diagnostics(errors)
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw,)))
    typed = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool, base_files=base_files, diagnostics=diagnostics, mode="shadow"
        )
    ).to_dict()
    raw_plan = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool, base_files=base_files, artifact_quality_errors=(raw,), mode="shadow"
        )
    ).to_dict()

    assert [item.code for item in diagnostics] == codes
    assert coverage.total_diagnostics == len(codes)
    assert coverage.covered_diagnostic_count == len(codes)
    assert coverage.uncovered_diagnostic_count == 0
    assert source_tool in {tool for item in coverage.items for tool in item.matched_source_tools}
    assert all(item.metadata["verifier_command"] == command for item in diagnostics)
    causal = diagnostics[-1]
    assert command in causal.metadata["raw"]
    assert output in causal.metadata["raw"]
    assert typed["planned"] is True
    assert typed["composition_summary"] == raw_plan["composition_summary"]
    assert typed["agi_execution_authority"] is False
    assert typed["director_tool_execution_required"] is True
    assert (errors, base_files) == original
    assert list(tmp_path.iterdir()) == []
    if len(codes) == 2:
        compiler = diagnostics[0]
        assert compiler.path == "src/main.ts"
        assert compiler.metadata["line"] == 2
        assert compiler.metadata["column"] == 1
        assert compiler.metadata["raw"] == output.splitlines()[-1]
        assert typed["composition_summary"]["changed_paths"] == ["package.json"]
        assert (
            '"start": "npm run build && node dist/main.js"'
            in typed["composition_summary"]["patches"][0]["content_after"]
        )
    elif "EADDRINUSE" in output:
        assert typed["composition_summary"]["changed_paths"] == ["package.json"]
        assert "${PORT:-0}" in typed["composition_summary"]["patches"][0]["content_after"]
    # Missing-target plan completeness remains governed by the existing broad
    # contract tests; this regression proves provenance, not target delivery.


@pytest.mark.parametrize(
    "command",
    ['npm test -- "test(a)"', "npm test -- $(printf '(test)')", r'npm test -- "test(\"quoted\")"'],
)
def test_balanced_nested_and_quoted_commands_are_data(command: str) -> None:
    output = "Could not find './test'"
    diagnostics = normalize_director_repair_diagnostics(
        (f"workspace validation command failed ({command}):\n{output}",)
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].metadata["verifier_command"] == command
    assert diagnostics[0].metadata["command"] == command
    assert output in diagnostics[0].metadata["raw"]
    assert diagnostics[0].metadata.get("allowed_paths") is None


@pytest.mark.parametrize(
    "raw",
    [
        "workspace validation command failed (npm test:\nopaque failure",
        'workspace validation command failed (npm test "unterminated):\nopaque failure',
        "workspace validation command failed (npm test) opaque failure",
        "workspace validation command failed (npm test):",
        "workspace validation command failed (): opaque failure",
        "opaque failure",
        "",
    ],
)
def test_invalid_or_empty_envelope_cannot_fabricate_verified_command_context(raw: str) -> None:
    diagnostics = normalize_director_repair_diagnostics((raw,))
    assert all("verifier_command" not in item.metadata for item in diagnostics)
    assert all(item.code != "javascript_module_error" for item in diagnostics)


def test_output_without_command_does_not_invent_npm_provenance() -> None:
    diagnostics = normalize_director_repair_diagnostics(("Could not find './test'\nopaque failure",))
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(artifact_quality_errors=("Could not find './test'\nopaque failure",))
    )
    assert coverage.covered_diagnostic_count == 0
    assert all("verifier_command" not in item.metadata for item in diagnostics)
    assert all("npm" not in item.metadata["raw"] for item in diagnostics)


@pytest.mark.parametrize("command", ["tsc --noEmit", "npm run build", "python -c \"print('npm test')\""])
def test_command_strings_cannot_override_compiler_identity_or_create_wrapper_diagnostics(command: str) -> None:
    output = "src/main.ts(2,1): error TS6133: 'Unused' is declared but its value is never read."
    plain = normalize_director_repair_diagnostics((output,))
    wrapped = normalize_director_repair_diagnostics((f"workspace validation command failed ({command}):\n{output}",))
    assert len(wrapped) == 1
    assert wrapped[0].code == "typescript_ts6133"
    assert wrapped[0].path == "src/main.ts"
    assert wrapped[0].metadata["diagnostic_id"] == plain[0].metadata["diagnostic_id"]
    assert wrapped[0].metadata["raw"] == output


def test_literal_path_escapes_and_typed_canonical_fields_remain_exact() -> None:
    raw = r"src\name.ts(3,4): error TS6133: 'Unused' is declared but its value is never read."
    typed = {
        "source": "compiler",
        "code": "typescript_ts5110",
        "path": r"config\nested.json",
        "message": "Invalid module configuration.",
        "raw": "workspace validation command failed (npm test): Could not find './test'",
        "metadata": {"line": 7, "column": 8},
    }
    original = deepcopy(typed)
    diagnostics = normalize_director_repair_diagnostics((f"workspace validation command failed (tsc):\n{raw}", typed))
    assert [(item.code, item.path) for item in diagnostics] == [
        ("typescript_ts6133", "src/name.ts"),
        ("typescript_ts5110", "config/nested.json"),
    ]
    assert diagnostics[0].metadata["raw"] == raw
    assert "verifier_command" not in diagnostics[1].metadata
    assert diagnostics[1].metadata["line"] == 7
    assert typed == original


def _unittest_output(names: tuple[str, ...]) -> str:
    return (
        "\n".join(
            f"{'=' * 70}\nFAIL: {name} (tests.test_checks.Checks)\n{'-' * 70}\n"
            f'Traceback (most recent call last):\n  File "tests/test_checks.py", line {index}, in {name}\n'
            f"    self.assertEqual(1, 2)\nAssertionError: failure {name}"
            for index, name in enumerate(names, 10)
        )
        + f"\n{'-' * 70}\nRan {len(names)} tests in 0.001s\nFAILED (failures={len(names)})"
    )


def test_multiline_python_command_never_becomes_fake_diagnostic_rows() -> None:
    script = "\n".join(f"print('npm test fake source row ({index})')" for index in range(79))
    output = _unittest_output(("test_alpha", "test_beta", "test_gamma"))
    diagnostics = normalize_director_repair_diagnostics(
        (f'workspace validation command failed (python -c "{script}"):\n{output}',)
    )
    assert len(diagnostics) == 3
    assert [item.metadata["test_name"] for item in diagnostics] == [
        "test_alpha (tests.test_checks.Checks)",
        "test_beta (tests.test_checks.Checks)",
        "test_gamma (tests.test_checks.Checks)",
    ]
    assert all(item.path == "tests/test_checks.py" for item in diagnostics)
    assert all("fake source row" not in item.metadata["raw"] for item in diagnostics)
    assert all("verifier_command" not in item.metadata for item in diagnostics)


def test_empty_output_never_parses_the_embedded_python_program() -> None:
    script = "\n".join(f"print('src/fake.ts({index},1): error TS6133: fake')" for index in range(1, 80))
    diagnostics = normalize_director_repair_diagnostics(
        (f'workspace validation command failed (python -c "{script}"):',)
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "workspace_validation_failed"
    assert diagnostics[0].path is None
    assert "verifier_command" not in diagnostics[0].metadata


@pytest.mark.parametrize("command_length", [1024, 1025])
def test_large_command_is_bounded_without_discarding_actual_output(command_length: int) -> None:
    command = "python -c " + "x" * (command_length - 10)
    output = "src/real.ts(3,4): error TS6133: 'Unused' is declared but its value is never read."
    diagnostics = normalize_director_repair_diagnostics(
        (f"workspace validation command failed ({command}):\n{output}",)
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].path == "src/real.ts"
    assert diagnostics[0].metadata["raw"] == output
    if command_length == 1024:
        assert diagnostics[0].metadata["verifier_command"] == command
    else:
        assert "verifier_command" not in diagnostics[0].metadata


def test_npm_words_in_another_program_cannot_route_a_loader_cycle() -> None:
    command = "python -c \"print('npm run start')\""
    output = "Error [ERR_REQUIRE_CYCLE_MODULE]: Cannot require() ES Module /tmp/demo/src/main.ts in a cycle."
    raw = f"workspace validation command failed ({command}):\n{output}"
    diagnostics = normalize_director_repair_diagnostics((raw,))
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=(raw,)))
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "javascript_module_error"
    assert diagnostics[0].metadata["verifier_command"] == command
    assert "npm run start" not in diagnostics[0].metadata["raw"]
    assert NPM_SCRIPT not in {tool for item in coverage.items for tool in item.matched_source_tools}


@pytest.mark.parametrize(
    ("command", "outputs"),
    [
        ("npm test", ["Could not find './test'", "Could not find 'dist/__tests__'"]),
        (
            "npm run start",
            ["Error: listen EADDRINUSE: 127.0.0.1:8080", "Error: listen EADDRINUSE: 127.0.0.1:8081"],
        ),
    ],
)
def test_distinct_verifier_failures_are_not_deduplicated_as_one_command_wrapper(
    command: str, outputs: list[str]
) -> None:
    errors = [f"workspace validation command failed ({command}):\n{output}" for output in outputs]
    diagnostics = normalize_director_repair_diagnostics(errors)
    assert len(diagnostics) == 2
    assert len({item.metadata["diagnostic_id"] for item in diagnostics}) == 2
    assert all(output in item.message for output, item in zip(outputs, diagnostics, strict=True))


@pytest.mark.parametrize(
    "argument",
    [
        "src/fake.ts(1,1): error TS6133: invented",
        "Error [ERR_REQUIRE_CYCLE_MODULE]: Cannot require() ES Module src/fake.js in a cycle.",
        "error[E0432]: unresolved import `invented`",
        "NameError: name 'invented' is not defined",
    ],
    ids=["typescript", "javascript", "rust", "python"],
)
@pytest.mark.parametrize(
    ("invocation", "output", "source_tool", "package"),
    [
        (
            "npm test",
            "Could not find './test'",
            MISSING_TARGET,
            '{"scripts":{"test":"node --test ./test"}}',
        ),
        (
            "npm run start",
            "Error: listen EADDRINUSE: address already in use 0.0.0.0:8080",
            NPM_SCRIPT,
            '{"scripts":{"start":"http-server . -p 8080"}}',
        ),
        (
            "npm run serve",
            "Error: listen EADDRINUSE: address already in use 0.0.0.0:8081",
            NPM_SCRIPT,
            '{"scripts":{"serve":"http-server . -p 8081"}}',
        ),
    ],
    ids=["missing-target", "start-port", "serve-port"],
)
def test_quoted_diagnostic_argument_is_data_for_public_normalization_and_coverage(
    argument: str,
    invocation: str,
    output: str,
    source_tool: str,
    package: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = f'{invocation} -- "{argument}"'
    raw = f"workspace validation command failed ({command}):\n{output}"
    errors = [raw]
    base_files = {"package.json": package}
    original = deepcopy((errors, base_files))
    monkeypatch.chdir(tmp_path)
    baseline = normalize_director_repair_diagnostics(
        (f"workspace validation command failed ({invocation}):\n{output}",)
    )
    diagnostics = normalize_director_repair_diagnostics(errors)
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(artifact_quality_errors=tuple(errors)))
    shadow = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=source_tool, base_files=base_files, diagnostics=diagnostics, mode="shadow"
        )
    ).to_dict()

    assert [(item.code, item.path) for item in diagnostics] == [("workspace_validation_failed", None)]
    assert diagnostics[0].message == baseline[0].message
    assert diagnostics[0].metadata["diagnostic_id"] == baseline[0].metadata["diagnostic_id"]
    assert diagnostics[0].metadata["verifier_command"] == command
    assert diagnostics[0].metadata["command"] == command
    assert len(command) <= 1024
    assert output in diagnostics[0].metadata["raw"]
    assert coverage.total_diagnostics == coverage.covered_diagnostic_count == 1
    assert coverage.uncovered_diagnostic_count == 0
    assert source_tool in coverage.items[0].matched_source_tools
    assert shadow["planned"] is True
    assert shadow["agi_execution_authority"] is False
    assert shadow["director_tool_execution_required"] is True
    assert diagnostics[0].metadata.get("allowed_paths") is None
    assert (errors, base_files) == original
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("invocation", ["npm test", "npm run start", "npm run serve"])
def test_actual_compiler_stdout_keeps_priority_over_quoted_diagnostic_argument(invocation: str) -> None:
    command = f'{invocation} -- "src/fake.ts(1,1): error TS6133: invented"'
    output = "src/real.ts(7,8): error TS6133: 'Unused' is declared but its value is never read."
    diagnostics = normalize_director_repair_diagnostics(
        (f"workspace validation command failed ({command}):\n{output}",)
    )
    assert [(item.code, item.path) for item in diagnostics] == [("typescript_ts6133", "src/real.ts")]
    assert diagnostics[0].metadata["line"] == 7
    assert diagnostics[0].metadata["column"] == 8
    assert diagnostics[0].metadata["raw"] == output
    assert diagnostics[0].metadata["verifier_command"] == command


@pytest.mark.parametrize(
    ("command", "output", "codes", "paths"),
    [
        (
            "npm test",
            "TAP version 13\nnot ok 1 - alpha\n  error: alpha failed\n  at tests/alpha.js:3:4\n"
            "ok 2 - passed\nnot ok 3 - beta\n  error: beta failed\n  at tests/beta.js:5:6\n1..3",
            ["verifier_test_failure", "verifier_test_failure"],
            ["tests/alpha.js", "tests/beta.js"],
        ),
        (
            "python -m unittest",
            _unittest_output(("test_alpha", "test_beta")),
            ["python_assertionerror", "python_assertionerror"],
            ["tests/test_checks.py", "tests/test_checks.py"],
        ),
        (
            "go test ./...",
            "--- FAIL: TestAlpha (0.00s)\n    alpha_test.go:12: wrong alpha\n"
            "--- FAIL: TestBeta (0.00s)\n    beta_test.go:18: wrong beta\nFAIL\nFAIL example 0.01s",
            ["verifier_test_failure", "verifier_test_failure"],
            ["alpha_test.go", "beta_test.go"],
        ),
        (
            "cargo test --quiet",
            "---- tests::alpha stdout ----\nthread 'tests::alpha' panicked at src/lib.rs:11:5:\n"
            "assertion failed: alpha\n---- tests::beta stdout ----\n"
            "thread 'tests::beta' panicked at src/lib.rs:22:5:\nassertion failed: beta\ntest result: FAILED",
            ["verifier_test_failure", "verifier_test_failure"],
            ["src/lib.rs", "src/lib.rs"],
        ),
        (
            "cargo check",
            "error[E0432]: unresolved import `alpha`\n --> src/alpha.rs:3:4\n"
            "error[E0063]: missing field `beta`\n --> src/beta.rs:5:6\nerror: could not compile `example`",
            ["rust_e0432", "rust_e0063"],
            ["src/alpha.rs", "src/beta.rs"],
        ),
    ],
    ids=["tap", "unittest", "go", "rust-tests", "rust-compiler"],
)
def test_real_failure_multiplicity_and_paths_survive_command_context(
    command: str, output: str, codes: list[str], paths: list[str]
) -> None:
    diagnostics = normalize_director_repair_diagnostics(
        (f"workspace validation command failed ({command}):\n{output}",)
    )
    assert [item.code for item in diagnostics] == codes
    assert [item.path for item in diagnostics] == paths
    assert len({item.metadata["diagnostic_id"] for item in diagnostics}) == 2
