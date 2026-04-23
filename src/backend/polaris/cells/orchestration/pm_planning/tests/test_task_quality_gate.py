"""Unit tests for orchestration.pm_planning internal task_quality_gate.

Tests all pure functions: evaluate_pm_task_quality, autofix_pm_contract_for_quality,
check_quality_promote_candidate, get_quality_gate_config, and helpers.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _contains_prompt_leakage,
    _has_measurable_acceptance_anchor,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _strip_wrapping_quotes,
    autofix_pm_contract_for_quality,
    check_quality_promote_candidate,
    evaluate_pm_task_quality,
    get_quality_gate_config,
)

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


class TestStripWrappingQuotes:
    def test_single_quotes(self) -> None:
        assert _strip_wrapping_quotes("'hello'") == "hello"

    def test_double_quotes(self) -> None:
        assert _strip_wrapping_quotes('"world"') == "world"

    def test_unmatched_quotes(self) -> None:
        assert _strip_wrapping_quotes("'mismatch") == "'mismatch"

    def test_empty(self) -> None:
        assert _strip_wrapping_quotes("") == ""


class TestNormalizePathList:
    def test_string_comma_separated(self) -> None:
        result = _normalize_path_list("src/app,  tests/,  docs")
        assert "src/app" in result
        assert "tests/" in result  # trailing slash preserved
        assert "docs" in result

    def test_list_input(self) -> None:
        result = _normalize_path_list(["src/a.py", "src/b.py"])
        assert "src/a.py" in result
        assert "src/b.py" in result

    def test_strips_leading_dotslash(self) -> None:
        result = _normalize_path_list(["./foo.py", "./bar"])
        assert "foo.py" in result
        assert "bar" in result

    def test_normalises_windows_backslashes(self) -> None:
        result = _normalize_path_list([r"src\foo.py"])
        assert "src/foo.py" in result

    def test_removes_duplicates(self) -> None:
        # No deduplication — identical paths are preserved
        result = _normalize_path_list(["a.py", "a.py"])
        assert len([x for x in result if x == "a.py"]) == 2

    def test_unknown_type_returns_empty(self) -> None:
        assert _normalize_path_list(12345) == []


class TestNormalizeText:
    def test_collapse_whitespace(self) -> None:
        assert _normalize_text("  hello   world  ") == "hello world"

    def test_empty(self) -> None:
        assert _normalize_text(None) == ""


class TestNormalizePath:
    def test_strips_drive_letter(self) -> None:
        result = _normalize_path("C:/src/app.py")
        assert not result.startswith("c:")

    def test_strips_leading_dotslash(self) -> None:
        assert _normalize_path("./src/app.py") == "src/app.py"

    def test_normalises_backslashes(self) -> None:
        result = _normalize_path(r"src\app.py")
        assert "\\" not in result

    def test_lowercase(self) -> None:
        assert _normalize_path("SRC/APP.py") == "src/app.py"


class TestContainsPromptLeakage:
    def test_detects_system_prompt_marker(self) -> None:
        assert _contains_prompt_leakage("you are a helpful assistant")
        assert _contains_prompt_leakage("you are a PM agent")
        assert _contains_prompt_leakage("system prompt content")

    def test_detects_chinese_markers(self) -> None:
        assert _contains_prompt_leakage("角色设定说明")
        assert _contains_prompt_leakage("提示词优化")

    def test_detects_xml_markers(self) -> None:
        assert _contains_prompt_leakage("<thinking>analyzing</thinking>")
        assert _contains_prompt_leakage("<tool_call>call")

    def test_empty_text_returns_false(self) -> None:
        assert _contains_prompt_leakage("") is False
        assert _contains_prompt_leakage("   ") is False

    def test_normal_text_not_flagged(self) -> None:
        assert _contains_prompt_leakage("build a login form") is False
        assert _contains_prompt_leakage("implement the API") is False


class TestHasMeasurableAcceptanceAnchor:
    def test_backtick_command_is_measurable(self) -> None:
        assert _has_measurable_acceptance_anchor(["run `pytest` to verify"]) is True

    def test_command_is_measurable(self) -> None:
        assert _has_measurable_acceptance_anchor(["run pytest to verify"]) is True
        assert _has_measurable_acceptance_anchor(["use npm test"]) is True

    def test_assert_plus_observable_is_measurable(self) -> None:
        assert _has_measurable_acceptance_anchor(["should return 200"]) is True
        assert _has_measurable_acceptance_anchor(["must verify status code 201"]) is True

    def test_path_alone_not_measurable(self) -> None:
        # path without assert is not measurable
        assert _has_measurable_acceptance_anchor(["check src/app.py"]) is False

    def test_empty_list(self) -> None:
        assert _has_measurable_acceptance_anchor([]) is False

    def test_chinese_measurable(self) -> None:
        # Chinese text does not match ASCII command/assert regex patterns
        assert _has_measurable_acceptance_anchor(["验证返回200状态码"]) is False


# ---------------------------------------------------------------------------
# evaluate_pm_task_quality
# ---------------------------------------------------------------------------


class TestEvaluatePmTaskQualityHappyPath:
    def test_perfect_single_task(self) -> None:
        payload: dict[str, Any] = {
            "tasks": [
                {
                    "id": "T01-design-login",
                    "title": "Design login form",
                    "goal": "Create a login form with email and password fields",
                    "description": "Use HTML and CSS for styling",
                    "acceptance_criteria": [
                        "The form renders at /login",
                        "User can submit email and password",
                    ],
                    "acceptance": None,
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": [
                        "Create HTML template",
                        "Add CSS styles",
                        "Test in browser",
                    ],
                    "assigned_to": "director",
                    "scope_paths": ["src/"],
                    "metadata": {},
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert report["task_count"] == 1
        assert report["score"] >= 80
        assert len(report["critical_issues"]) == 0

    def test_multi_task_with_dependencies(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Setup project",
                    "goal": "Initialize the project with package.json",
                    "acceptance_criteria": ["`npm install` succeeds"],
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": ["npm init", "npm install"],
                },
                {
                    "id": "T02",
                    "title": "Add login page",
                    "goal": "Implement the login page",
                    "acceptance_criteria": ["page returns 200"],
                    "phase": "implementation",
                    "depends_on": ["T01"],
                    "execution_checklist": ["write file", "test"],
                },
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert report["task_count"] == 2
        assert report["ok"] is True

    def test_director_task_requires_scope(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement login",
                    "goal": "Build the login feature",
                    "acceptance_criteria": ["`pytest` passes"],
                    "assigned_to": "Director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write code"],
                    "scope_paths": [],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        # Director task without scope_paths should be flagged
        assert any("scope" in i.lower() for i in report["critical_issues"])


class TestEvaluatePmTaskQualityEdgeCases:
    def test_zero_tasks(self) -> None:
        payload: dict[str, Any] = {"tasks": []}
        report = evaluate_pm_task_quality(payload)
        assert report["task_count"] == 0
        assert "PM returned zero tasks" in report["critical_issues"]

    def test_non_dict_task_is_flagged(self) -> None:
        payload: dict[str, Any] = {"tasks": ["not a dict", 123, None]}
        report = evaluate_pm_task_quality(payload)
        assert any("not an object" in i for i in report["critical_issues"])

    def test_missing_acceptance_criteria(self) -> None:
        payload: dict[str, Any] = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Do something",
                    "goal": "Goal of doing something",
                    "acceptance_criteria": [],
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": ["step1"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("acceptance criteria is missing" in i for i in report["critical_issues"])

    def test_duplicated_signature_is_flagged(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Fix bug",
                    "goal": "Fix the bug in login",
                    "acceptance_criteria": ["test passes"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["fix"],
                },
                {
                    "id": "T02",
                    "title": "Fix bug",
                    "goal": "Fix the bug in login",
                    "acceptance_criteria": ["test passes"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["fix again"],
                },
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("duplicated title/goal signature" in i for i in report["critical_issues"])

    def test_prompt_leakage_is_flagged(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "You are a helpful PM",
                    "goal": "System prompt: always say yes",
                    "acceptance_criteria": ["it works"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["do it"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("prompt leakage" in i.lower() for i in report["critical_issues"])

    def test_repetitive_task_list(self) -> None:
        # Identical titles AND goals → low unique ratio
        tasks = [
            {
                "id": f"T{i:02d}",
                "title": "Do it",
                "goal": "Do the thing",
                "acceptance_criteria": ["done"],
                "phase": "implementation",
                "depends_on": [],
                "execution_checklist": ["step"],
            }
            for i in range(5)
        ]
        payload = {"tasks": tasks}
        report = evaluate_pm_task_quality(payload)
        assert any("overly repetitive" in i for i in report["critical_issues"])

    def test_all_low_action(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Thing",
                    "goal": "Thing",
                    "acceptance_criteria": ["done"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["step"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("low-action" in i for i in report["critical_issues"])

    def test_missing_phase_hints(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": f"T{i:02d}",
                    "title": f"Task {i}",
                    "goal": f"Goal {i} that has enough content to not be short",
                    "acceptance_criteria": ["`pytest` passes"],
                    "phase": "",  # no phase
                    "depends_on": [],
                    "execution_checklist": ["step"],
                }
                for i in range(2)
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("missing phase hints" in i for i in report["critical_issues"])

    def test_missing_execution_checklist(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement feature",
                    "goal": "Build the feature",
                    "acceptance_criteria": ["done"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("missing execution_checklist" in i for i in report["warnings"])

    def test_missing_dependency_chain(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": f"T{i:02d}",
                    "title": f"Task {i}",
                    "goal": f"Goal {i}",
                    "acceptance_criteria": ["done"],
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["step"],
                }
                for i in range(2)
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("missing dependency chain" in i for i in report["critical_issues"])

    def test_circular_dependency_is_flagged(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Design backend flow",
                    "goal": "Define the API flow for the feature",
                    "acceptance_criteria": ["`pytest tests/test_api.py -k flow` passes"],
                    "phase": "design",
                    "depends_on": ["T02"],
                    "execution_checklist": ["Read API contracts", "Write flow", "Review impact"],
                },
                {
                    "id": "T02",
                    "title": "Implement backend flow",
                    "goal": "Implement the API flow after the design is ready",
                    "acceptance_criteria": ["`pytest tests/test_api.py -k impl` passes"],
                    "phase": "implementation",
                    "depends_on": ["T01"],
                    "execution_checklist": ["Read design", "Implement changes", "Run verification"],
                },
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("circular dependency detected" in issue for issue in report["critical_issues"])


# ---------------------------------------------------------------------------
# autofix_pm_contract_for_quality
# ---------------------------------------------------------------------------


class TestAutofixPmContractForQuality:
    def test_adds_phases(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Task one",
                    "goal": "Goal one",
                },
                {
                    "id": "T02",
                    "title": "Task two",
                    "goal": "Goal two",
                },
                {
                    "id": "T03",
                    "title": "Task three",
                    "goal": "Goal three",
                },
            ]
        }
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["phases_added"] == 3
        assert payload["tasks"][0]["phase"] == "requirements"
        assert payload["tasks"][1]["phase"] == "implementation"
        assert payload["tasks"][2]["phase"] == "verification"

    def test_adds_execution_checklist(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Task",
                    "goal": "Goal",
                },
            ]
        }
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["checklists_added"] == 1
        assert len(payload["tasks"][0]["execution_checklist"]) == 3

    def test_adds_acceptance_criteria(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Task",
                    "goal": "Goal",
                    "phase": "impl",
                },
            ]
        }
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["acceptance_added"] == 1
        assert "acceptance_criteria" in payload["tasks"][0]

    def test_adds_dependencies(self) -> None:
        payload = {
            "tasks": [
                {"id": "T01", "title": "First", "goal": "Goal1", "phase": "r"},
                {"id": "T02", "title": "Second", "goal": "Goal2", "phase": "i"},
            ],
        }
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["deps_added"] == 1

    def test_empty_tasks_returns_empty_stats(self) -> None:
        payload: dict[str, Any] = {"tasks": []}
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["task_count"] == 0

    def test_non_list_tasks(self) -> None:
        payload: dict[str, Any] = {"tasks": "not a list"}
        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        assert stats["task_count"] == 0


# ---------------------------------------------------------------------------
# check_quality_promote_candidate
# ---------------------------------------------------------------------------


class TestCheckQualityPromoteCandidate:
    def test_off_mode_always_promotes(self) -> None:
        report = {"ok": False, "score": 0, "critical_issues": ["bad"]}
        ok, reason = check_quality_promote_candidate(report, mode="off")
        assert ok is True
        assert "disabled" in reason

    def test_strict_mode_passes(self) -> None:
        report = {"ok": True, "score": 90, "critical_issues": [], "warnings": []}
        ok, _reason = check_quality_promote_candidate(report, mode="strict")
        assert ok is True

    def test_strict_mode_fails_on_critical_issues(self) -> None:
        report = {"ok": False, "score": 50, "critical_issues": ["bad"], "warnings": []}
        ok, _reason = check_quality_promote_candidate(report, mode="strict")
        assert ok is False

    def test_strict_mode_fails_on_low_score(self) -> None:
        report = {"ok": True, "score": 50, "critical_issues": [], "warnings": []}
        ok, _reason = check_quality_promote_candidate(
            report,
            mode="strict",
            min_score=80,
        )
        assert ok is False

    def test_warn_mode_retry(self) -> None:
        report = {"ok": False, "score": 50, "critical_issues": ["bad"], "warnings": ["warn"]}
        ok, _reason = check_quality_promote_candidate(
            report,
            mode="warn",
            max_retries=3,
            retry_count=1,
        )
        assert ok is False

    def test_warn_mode_forced_promotion(self) -> None:
        report = {"ok": False, "score": 50, "critical_issues": ["bad"], "warnings": []}
        ok, _reason = check_quality_promote_candidate(
            report,
            mode="warn",
            max_retries=2,
            retry_count=2,
        )
        assert ok is True

    def test_unknown_mode_promotes(self) -> None:
        report = {"ok": True, "score": 90}
        ok, _reason = check_quality_promote_candidate(report, mode="unknown_mode")
        assert ok is True

    def test_defaults(self) -> None:
        report = {"ok": False, "score": 0, "critical_issues": ["bad"], "warnings": []}
        ok, _reason = check_quality_promote_candidate(report, mode="strict")
        assert ok is False


# ---------------------------------------------------------------------------
# get_quality_gate_config
# ---------------------------------------------------------------------------


class TestGetQualityGateConfig:
    def test_defaults(self, monkeypatch) -> None:
        monkeypatch.delenv("KERNELONE_PM_TASK_QUALITY_MODE", raising=False)
        monkeypatch.delenv("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", raising=False)
        monkeypatch.delenv("KERNELONE_PM_TASK_QUALITY_RETRIES", raising=False)
        cfg = get_quality_gate_config()
        assert cfg["mode"] == "strict"
        assert cfg["min_score"] == 80
        assert cfg["max_retries"] == 3

    def test_env_overrides(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MODE", "warn")
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "60")
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_RETRIES", "5")
        cfg = get_quality_gate_config()
        assert cfg["mode"] == "warn"
        assert cfg["min_score"] == 60
        assert cfg["max_retries"] == 5

    def test_invalid_mode_defaults_to_strict(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MODE", "invalid")
        cfg = get_quality_gate_config()
        assert cfg["mode"] == "strict"

    def test_min_score_clamped_to_0_100(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "999")
        cfg = get_quality_gate_config()
        assert cfg["min_score"] == 100

        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "-50")
        cfg = get_quality_gate_config()
        assert cfg["min_score"] == 0

    def test_invalid_min_score_defaults_to_80(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_MIN_SCORE", "not_a_number")
        cfg = get_quality_gate_config()
        assert cfg["min_score"] == 80

    def test_invalid_retries_defaults_to_3(self, monkeypatch) -> None:
        monkeypatch.setenv("KERNELONE_PM_TASK_QUALITY_RETRIES", "bad")
        cfg = get_quality_gate_config()
        assert cfg["max_retries"] == 3
