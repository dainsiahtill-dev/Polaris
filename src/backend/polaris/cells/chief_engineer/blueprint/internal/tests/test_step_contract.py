"""Unit tests for the construction-step contract and CE-stage quality gate."""

from __future__ import annotations

from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    CE_BLUEPRINT_TASKS_SCHEMA_VERSION,
    build_blueprint_tasks_contract,
    normalize_construction_step,
    validate_construction_steps,
)


def _step(**overrides):
    base = {
        "step_id": "PM-1-S1",
        "target_file": "app.js",
        "est_lines": 80,
        "signatures": ["function render(state)"],
        "interface_names": ["#editor"],
        "verify": "node --check app.js",
        "depends_on": [],
    }
    base.update(overrides)
    return base


class TestNormalize:
    def test_defaults_and_coercion(self) -> None:
        step = normalize_construction_step(
            {"file": "./src/app.js", "est_lines": "90", "verify": "node --check src/app.js"},
            parent_pm_task="PM-1",
            index=0,
        )
        assert step["step_id"] == "PM-1-S1"
        assert step["target_file"] == "src/app.js"
        assert step["est_lines"] == 90
        assert step["parent_pm_task"] == "PM-1"

    def test_normalizes_verify_before_market_publish(self) -> None:
        step = normalize_construction_step(
            {
                "file": "./src/app.py",
                "est_lines": "20",
                "verify": "pytest -k test_create_app 通过，验证服务工厂可创建",
            },
            parent_pm_task="PM-1",
            index=0,
        )

        assert step["verify"] == "pytest -k test_create_app"

    def test_non_dict_input_yields_empty_shape(self) -> None:
        step = normalize_construction_step("prose", parent_pm_task="PM-1", index=2)
        assert step["step_id"] == "PM-1-S3"
        assert step["target_file"] == ""


class TestGate:
    def test_valid_steps_pass(self) -> None:
        steps = [_step(), _step(step_id="PM-1-S2", target_file="style.css", depends_on=["PM-1-S1"])]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []

    def test_empty_fission_blocked(self) -> None:
        errors = validate_construction_steps([], parent_pm_task="PM-1")
        assert errors and "no construction steps" in errors[0]

    def test_oversized_step_blocked(self) -> None:
        errors = validate_construction_steps([_step(est_lines=121)], parent_pm_task="PM-1")
        assert any("exceeds the convergence ceiling" in e for e in errors)

    def test_missing_verify_blocked(self) -> None:
        errors = validate_construction_steps([_step(verify="")], parent_pm_task="PM-1")
        assert any("machine-executable verify" in e for e in errors)

    def test_missing_signatures_blocked(self) -> None:
        errors = validate_construction_steps([_step(signatures=[])], parent_pm_task="PM-1")
        assert any("signatures skeleton" in e for e in errors)

    def test_all_hollow_verify_for_code_target_blocked(self) -> None:
        # I3-r21: existence-only verify let a code step "resolve" on a stub.
        errors = validate_construction_steps(
            [_step(target_file="main.js", verify="test -f main.js && grep -q 'main.js' main.js")],
            parent_pm_task="PM-1",
        )
        assert any("all-hollow" in e for e in errors)

    def test_syntax_check_verify_passes(self) -> None:
        # The real S3 verify: node --check is a structural clause.
        errors = validate_construction_steps(
            [_step(target_file="main.js", verify="test -f main.js && node --check main.js")],
            parent_pm_task="PM-1",
        )
        assert errors == []

    def test_arithmetic_oracle_mismatch_blocked(self) -> None:
        errors = validate_construction_steps(
            [
                _step(
                    target_file="calculator.py",
                    signatures=["def calculate(expression: str) -> float"],
                    verify=(
                        'python3 -c "import calculator; '
                        "print(calculator.calculate('1+2*(3-4)/5'))\" | grep -q -- '-0.2'"
                    ),
                )
            ],
            parent_pm_task="PM-1",
        )
        assert any("arithmetic oracle mismatch" in e and "evaluates to 0.6" in e for e in errors)

    def test_arithmetic_oracle_match_passes(self) -> None:
        errors = validate_construction_steps(
            [
                _step(
                    target_file="calculator.py",
                    signatures=["def calculate(expression: str) -> float"],
                    verify=(
                        'python3 -c "import calculator; '
                        "print(calculator.calculate('1+2*(3-4)/5'))\" | grep -q -- '0.6'"
                    ),
                )
            ],
            parent_pm_task="PM-1",
        )
        assert errors == []

    def test_signature_grep_verify_passes(self) -> None:
        # A grep for a DECLARED signature token is structural, not hollow.
        errors = validate_construction_steps(
            [
                _step(
                    target_file="main.js",
                    signatures=["class Paddle"],
                    verify="test -f main.js && grep -q 'class Paddle' main.js",
                )
            ],
            parent_pm_task="PM-1",
        )
        assert errors == []

    def test_doc_target_existence_only_verify_exempt(self) -> None:
        # readme.md has no signatures to assert — existence-only verify is fine.
        errors = validate_construction_steps(
            [_step(target_file="readme.md", signatures=[], verify="test -f readme.md")],
            parent_pm_task="PM-1",
        )
        assert errors == []

    def test_style_target_existence_only_verify_exempt(self) -> None:
        errors = validate_construction_steps(
            [_step(target_file="style.css", signatures=[], verify="test -f style.css")],
            parent_pm_task="PM-1",
        )
        assert errors == []

    def test_unknown_dependency_blocked(self) -> None:
        errors = validate_construction_steps([_step(depends_on=["PM-1-S9"])], parent_pm_task="PM-1")
        assert any("unknown step" in e for e in errors)

    def test_self_dependency_blocked(self) -> None:
        errors = validate_construction_steps([_step(depends_on=["PM-1-S1"])], parent_pm_task="PM-1")
        assert any("depends on itself" in e for e in errors)

    def test_duplicate_step_id_blocked(self) -> None:
        errors = validate_construction_steps([_step(), _step()], parent_pm_task="PM-1")
        assert any("duplicate step_id" in e for e in errors)

    def test_dependency_cycle_blocked(self) -> None:
        """A 2-cycle passes the unknown-ref and self-dep checks but would
        deadlock the market readiness gate forever — must be refused here."""
        steps = [
            _step(step_id="PM-1-S1", depends_on=["PM-1-S2"]),
            _step(step_id="PM-1-S2", target_file="b.js", depends_on=["PM-1-S1"]),
        ]
        errors = validate_construction_steps(steps, parent_pm_task="PM-1")
        assert any("cycle" in e for e in errors)

    def test_three_node_dependency_cycle_blocked(self) -> None:
        steps = [
            _step(step_id="PM-1-S1", depends_on=["PM-1-S3"]),
            _step(step_id="PM-1-S2", target_file="b.js", depends_on=["PM-1-S1"]),
            _step(step_id="PM-1-S3", target_file="c.js", depends_on=["PM-1-S2"]),
        ]
        errors = validate_construction_steps(steps, parent_pm_task="PM-1")
        assert any("cycle" in e for e in errors)

    def test_list_verify_joined_into_one_command(self) -> None:
        """Cloud models drift between string and array verify shapes — a
        bare str() turns the array into Python-repr garbage that bash can
        never pass (live I3-r10 poisoned every QA check of the run).

        The array is joined with `` && `` into a single runnable command. The
        ``grep -q`` clause is additionally hardened to ``grep -Fq`` because its
        pattern (``id="gameCanvas"``) is a literal — this fixed-string
        normalization is owned by ``_normalize_literal_grep_clauses`` and
        covered directly in test_step_verify; here it is incidental to the join.
        """
        step = normalize_construction_step(
            {
                "step_id": "S1",
                "target_file": "index.html",
                "verify": ["test -f ./index.html", "grep -q 'id=\"gameCanvas\"' ./index.html"],
            },
            parent_pm_task="PM-1",
            index=0,
        )
        assert step["verify"] == "test -f ./index.html && grep -Fq 'id=\"gameCanvas\"' ./index.html"

    def test_empty_list_verify_blocked_by_gate(self) -> None:
        step = normalize_construction_step(
            {"step_id": "S1", "target_file": "readme.md", "est_lines": 30, "verify": []},
            parent_pm_task="PM-1",
            index=0,
        )
        errors = validate_construction_steps([step], parent_pm_task="PM-1")
        assert any("machine-executable verify" in e for e in errors)

    def test_diamond_dependency_dag_passes(self) -> None:
        steps = [
            _step(step_id="PM-1-S1"),
            _step(step_id="PM-1-S2", target_file="b.js", depends_on=["PM-1-S1"]),
            _step(step_id="PM-1-S3", target_file="c.js", depends_on=["PM-1-S1"]),
            _step(step_id="PM-1-S4", target_file="d.js", depends_on=["PM-1-S2", "PM-1-S3"]),
        ]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []


class TestRefinements:
    def test_bare_step_id_namespaced_under_parent(self) -> None:
        step = normalize_construction_step({"step_id": "S1", "target_file": "a.js"}, parent_pm_task="PM-7", index=0)
        assert step["step_id"] == "PM-7-S1"

    def test_prefixed_step_id_untouched(self) -> None:
        step = normalize_construction_step(
            {"step_id": "PM-7-S2", "target_file": "a.js"}, parent_pm_task="PM-7", index=1
        )
        assert step["step_id"] == "PM-7-S2"

    def test_bare_depends_on_namespaced_under_parent(self) -> None:
        step = normalize_construction_step(
            {"step_id": "S2", "target_file": "a.js", "depends_on": ["S1"]}, parent_pm_task="PM-7", index=1
        )
        assert step["depends_on"] == ["PM-7-S1"]

    def test_prefixed_depends_on_untouched(self) -> None:
        step = normalize_construction_step(
            {"step_id": "S2", "target_file": "a.js", "depends_on": ["PM-7-S1"]}, parent_pm_task="PM-7", index=1
        )
        assert step["depends_on"] == ["PM-7-S1"]

    def test_bare_id_fission_with_dependency_chain_passes_gate(self) -> None:
        """Regression (live I3-r8): a fully-bare model fission (step_id S1..S4,
        depends_on referencing bare siblings) must normalize consistently and
        pass the gate instead of dying on manufactured 'unknown step' errors."""
        raw = [
            {"step_id": "S1", "target_file": "index.html", "est_lines": 30, "verify": "test -f index.html"},
            {
                "step_id": "S2",
                "target_file": "main.js",
                "est_lines": 110,
                "signatures": ["function loadLevel(n)"],
                "verify": "node --check main.js",
                "depends_on": ["S1"],
            },
            {
                "step_id": "S3",
                "target_file": "readme.md",
                "est_lines": 40,
                "verify": "test -f readme.md",
                "depends_on": ["S2"],
            },
        ]
        steps = [normalize_construction_step(item, parent_pm_task="PM-0001-2", index=i) for i, item in enumerate(raw)]
        assert validate_construction_steps(steps, parent_pm_task="PM-0001-2") == []
        assert steps[1]["depends_on"] == ["PM-0001-2-S1"]
        assert steps[2]["depends_on"] == ["PM-0001-2-S2"]

    def test_doc_step_passes_without_signatures(self) -> None:
        steps = [_step(step_id="PM-1-S1", target_file="readme.md", signatures=[], verify="verify ./readme.md exists")]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []

    def test_css_step_passes_without_signatures(self) -> None:
        steps = [_step(step_id="PM-1-S1", target_file="style.css", signatures=[])]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []

    def test_code_step_still_requires_signatures(self) -> None:
        errors = validate_construction_steps(
            [_step(signatures=[])],
            parent_pm_task="PM-1",
        )
        assert any("signatures skeleton" in e for e in errors)


def test_contract_assembly() -> None:
    steps = [_step()]
    contract = build_blueprint_tasks_contract(
        parent_pm_task="PM-1",
        blueprint_id="bp-1",
        blueprint_path="runtime/blueprints/bp-1.json",
        steps=steps,
    )
    assert contract["schema_version"] == CE_BLUEPRINT_TASKS_SCHEMA_VERSION
    assert contract["parent_task_id"] == "PM-1"
    assert contract["step_count"] == 1


class TestTargetFileShape:
    """对抗复核 D-fix: 非单文件相对路径的 target_file 会被执行侧 enum 钉靶拒绝,
    必须在 CE 门拦下交给 corrective re-ask, 而不是烧掉一次 Director 尝试。"""

    def test_glob_target_is_refused(self) -> None:
        errors = validate_construction_steps(
            [_step(target_file="src/*.js")],
            parent_pm_task="PM-1",
        )
        assert any("single relative file path" in e for e in errors)

    def test_comma_list_target_is_refused(self) -> None:
        errors = validate_construction_steps(
            [_step(target_file="a.js, b.js")],
            parent_pm_task="PM-1",
        )
        assert any("single relative file path" in e for e in errors)

    def test_absolute_and_escaping_targets_are_refused(self) -> None:
        for target in ("/etc/passwd", "~/x.js", "../outside.js"):
            errors = validate_construction_steps(
                [_step(target_file=target)],
                parent_pm_task="PM-1",
            )
            assert any("stay inside the workspace" in e for e in errors), target

    def test_clean_subdir_target_passes(self) -> None:
        steps = [_step(target_file="src/game/main.js")]
        assert validate_construction_steps(steps, parent_pm_task="PM-1") == []
