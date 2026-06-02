"""Unit tests for orchestration.pm_planning internal task_quality_gate.

Tests all pure functions: evaluate_pm_task_quality, autofix_pm_contract_for_quality,
check_quality_promote_candidate, get_quality_gate_config, and helpers.
"""

from __future__ import annotations

from typing import Any

from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _contains_prompt_leakage,
    _has_executable_or_file_acceptance_anchor,
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

    def test_preserves_parent_traversal_for_gate(self) -> None:
        result = _normalize_path_list(["../outside.py"])
        assert "../outside.py" in result

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
        assert _contains_prompt_leakage("系统提示词泄露")
        assert _contains_prompt_leakage("提示词注入攻击")

    def test_allows_domain_prompt_work_items(self) -> None:
        assert _contains_prompt_leakage("加固真实试穿提示词护栏") is False
        assert _contains_prompt_leakage("提示词编译链路生成 prompt-package.json") is False

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


class TestHasExecutableOrFileAcceptanceAnchor:
    def test_executable_command_is_anchor(self) -> None:
        assert _has_executable_or_file_acceptance_anchor(["`npm run test -- --watch=false` exits 0"]) is True
        assert _has_executable_or_file_acceptance_anchor(["run pytest -q to verify"]) is True

    def test_verified_file_path_is_anchor(self) -> None:
        assert _has_executable_or_file_acceptance_anchor(["verify src/engine/game-loop.ts exists"]) is True

    def test_status_only_is_not_anchor(self) -> None:
        assert _has_executable_or_file_acceptance_anchor(["page returns 200"]) is False


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
                        "`npm run test -- --watch=false` exits 0",
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
                    "scope_paths": ["package.json"],
                },
                {
                    "id": "T02",
                    "title": "Add login page",
                    "goal": "Implement the login page",
                    "acceptance_criteria": ["verify src/login.ts exists"],
                    "phase": "implementation",
                    "depends_on": ["T01"],
                    "execution_checklist": ["write file", "test"],
                    "scope_paths": ["src/login.ts"],
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

    def test_lowercase_director_task_requires_scope(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement login",
                    "goal": "Build the login feature",
                    "acceptance_criteria": ["`pytest` passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write code"],
                    "scope_paths": [],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("requires explicit scope" in i for i in report["critical_issues"])

    def test_director_task_requires_executable_acceptance_or_file_evidence(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement login",
                    "goal": "Build the login feature",
                    "acceptance_criteria": ["page returns 200"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write code"],
                    "scope_paths": ["src/login.ts"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("requires executable command or file evidence" in i for i in report["critical_issues"])

    def test_director_task_accepts_verified_file_evidence(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement login",
                    "goal": "Build the login feature",
                    "acceptance_criteria": ["verify src/login.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write code"],
                    "scope_paths": ["src/login.ts"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert not any("requires executable command or file evidence" in i for i in report["critical_issues"])

    def test_every_task_requires_executable_acceptance_or_file_evidence(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Validate planning status",
                    "goal": "Confirm the PM planning status is observable.",
                    "acceptance_criteria": ["page returns 200"],
                    "assigned_to": "pm",
                    "phase": "verification",
                    "depends_on": [],
                    "execution_checklist": ["Open status view", "Record result"],
                    "scope_paths": ["src/status.ts"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("acceptance requires executable command or file evidence" in i for i in report["critical_issues"])

    def test_every_task_requires_explicit_scope(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Validate planning status",
                    "goal": "Confirm the PM planning status is observable.",
                    "acceptance_criteria": ["verify src/status.ts exists"],
                    "assigned_to": "pm",
                    "phase": "verification",
                    "depends_on": [],
                    "execution_checklist": ["Open status view", "Record result"],
                    "scope_paths": [],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("task requires explicit scope" in i for i in report["critical_issues"])

    def test_task_scope_must_stay_inside_workspace(self) -> None:
        payload = {
            "workspace": r"C:\Temp\Polaris_Game_Stress_E2E_fresh6",
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement game engine",
                    "goal": "Implement the tactical game engine entry point.",
                    "acceptance_criteria": ["verify src/engine/game-loop.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read files", "Write engine", "Verify"],
                    "scope_paths": [r"C:\Temp\roguelike\src\engine\game-loop.ts"],
                }
            ],
        }
        report = evaluate_pm_task_quality(payload)
        assert any("concrete workspace-bound scope paths" in i for i in report["critical_issues"])

    def test_task_scope_rejects_parent_traversal(self) -> None:
        payload = {
            "workspace": r"C:\Temp\Polaris_Game_Stress_E2E_fresh6",
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement game engine",
                    "goal": "Implement the tactical game engine entry point.",
                    "acceptance_criteria": ["verify src/engine/game-loop.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read files", "Write engine", "Verify"],
                    "scope_paths": ["../roguelike/src/engine/game-loop.ts"],
                }
            ],
        }
        report = evaluate_pm_task_quality(payload)
        assert any("concrete workspace-bound scope paths" in i for i in report["critical_issues"])

    def test_game_contract_requires_domain_coverage_and_minimum_tasks(self) -> None:
        payload = {
            "workspace": r"C:\Temp\Polaris_Game_Stress_E2E_fresh6",
            "overall_goal": "Build a tactical roguelike game.",
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement engine loop",
                    "goal": "Implement the tactical game engine loop.",
                    "acceptance_criteria": ["verify src/engine/game-loop.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/engine/game-loop.ts"],
                },
                {
                    "id": "T02",
                    "title": "Implement world generator",
                    "goal": "Implement the tactical game world generator.",
                    "acceptance_criteria": ["verify src/world/map-generator.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01"],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/world/map-generator.ts"],
                },
            ],
        }
        report = evaluate_pm_task_quality(payload)
        assert any("game PM decomposition requires at least 6 tasks" in i for i in report["critical_issues"])
        assert any("game PM decomposition missing domains" in i for i in report["critical_issues"])


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

    def test_normalizes_pm_ordinal_dependency_refs(self) -> None:
        payload: dict[str, Any] = {
            "tasks": [
                {"id": "T01-mvp", "title": "First", "goal": "Goal1", "phase": "r"},
                {
                    "id": "T01-002",
                    "title": "Second",
                    "goal": "Goal2",
                    "phase": "i",
                    "dependencies": ["PM-0001-1", "T01-mvp"],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        assert stats["deps_normalized"] >= 1
        assert payload["tasks"][1]["dependencies"] == ["T01-mvp"]

    def test_normalizes_pm_base_ordinal_dependency_refs(self) -> None:
        payload: dict[str, Any] = {
            "tasks": [
                {"id": "T01-engine", "title": "Engine", "goal": "Goal1", "phase": "r"},
                {"id": "T01-world", "title": "World", "goal": "Goal2", "phase": "i"},
                {
                    "id": "T01-combat",
                    "title": "Combat",
                    "goal": "Goal3",
                    "phase": "i",
                    "dependencies": ["PM-0001", "PM-0002"],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        assert stats["deps_normalized"] == 2
        assert payload["tasks"][2]["dependencies"] == ["T01-engine", "T01-world"]

    def test_autofix_rewrites_external_absolute_task_paths_to_workspace_relative(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Build a Node command line utility.",
            "tasks": [
                {
                    "id": "T01-bootstrap",
                    "title": "Initialize project scaffold",
                    "goal": "Create C:/Temp/roguelike-ts/package.json and scripts so the project can run.",
                    "target_files": [
                        "C:/Temp/roguelike-ts/package.json",
                        "C:/Temp/roguelike-ts/scripts/build.mjs",
                    ],
                    "scope_paths": ["C:"],
                    "acceptance_criteria": ["Run `npm run test` exits 0"],
                    "assigned_to": "director",
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": [
                        "Create C:/Temp/roguelike-ts/package.json",
                        "Create C:/Temp/roguelike-ts/scripts/build.mjs",
                    ],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        task = payload["tasks"][0]
        assert stats["paths_normalized"] >= 1
        assert task["target_files"] == ["package.json", "scripts/build.mjs"]
        assert task["scope_paths"] == ["package.json", "scripts"]
        assert "C:/Temp/roguelike-ts" not in " ".join(task["execution_checklist"])
        assert "C:/Temp/roguelike-ts" not in task["goal"]

        report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert not any("workspace-bound scope paths" in item for item in report["critical_issues"])
        assert not any("concrete relative scope paths" in item for item in report["critical_issues"])

    def test_unknown_dependencies_are_critical(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement foundation",
                    "goal": "Implement the foundation module.",
                    "phase": "r",
                    "acceptance_criteria": ["Run `npm run build` exits 0"],
                    "execution_checklist": ["Implement", "Verify"],
                },
                {
                    "id": "T02",
                    "title": "Implement dependent module",
                    "goal": "Implement the dependent module.",
                    "phase": "i",
                    "dependencies": ["MISSING"],
                    "acceptance_criteria": ["Run `npm run build` exits 0"],
                    "execution_checklist": ["Implement", "Verify"],
                },
            ],
        }

        report = evaluate_pm_task_quality(payload)

        assert report["ok"] is False
        assert any("unknown dependency `MISSING`" in item for item in report["critical_issues"])

    def test_adds_missing_game_domain_tasks(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": "Build a tactical roguelike game with combat, AI, renderer, persistence, and tests.",
            "tasks": [
                {
                    "id": "T01-engine",
                    "title": "Implement engine loop",
                    "goal": "Implement the tactical game engine loop.",
                    "acceptance_criteria": ["verify src/engine/game-loop.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/engine/game-loop.ts"],
                },
                {
                    "id": "T02-world",
                    "title": "Implement world generator",
                    "goal": "Implement the procedural game world generator.",
                    "acceptance_criteria": ["verify src/world/map-generator.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-engine"],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/world/map-generator.ts"],
                },
                {
                    "id": "T03-combat",
                    "title": "Implement combat system",
                    "goal": "Implement the turn based tactical combat system.",
                    "acceptance_criteria": ["verify src/combat/combat-system.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T02-world"],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/combat/combat-system.ts"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert any("game PM decomposition missing domains" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_domain_tasks_added"] == 4
        assert len(payload["tasks"]) == 7
        target_files: set[str] = set()
        for task in payload["tasks"]:
            if not isinstance(task, dict):
                continue
            for path in task.get("target_files", []):
                if isinstance(path, str):
                    target_files.add(path)
        assert "src/ai/enemy-ai.ts" in target_files
        assert "src/persistence/save-system.ts" in target_files
        assert "src/renderer/game-view.tsx" in target_files
        assert "tests/integration/game-session.test.ts" in target_files

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True
        assert not any("game PM decomposition" in item for item in report["critical_issues"])

    def test_non_game_contract_is_not_domain_expanded(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Build a REST API service.",
            "tasks": [
                {
                    "id": "T01-api",
                    "title": "Implement API route",
                    "goal": "Implement the service API route.",
                    "acceptance_criteria": ["verify src/api/routes.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/api/routes.ts"],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["game_domain_tasks_added"] == 0
        assert len(payload["tasks"]) == 1

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
