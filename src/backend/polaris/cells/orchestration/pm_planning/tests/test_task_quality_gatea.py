"""Unit tests for orchestration.pm_planning internal task_quality_gate.

Tests all pure functions: evaluate_pm_task_quality, autofix_pm_contract_for_quality,
check_quality_promote_candidate, get_quality_gate_config, and helpers.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal.task_quality_gate import (
    _CARD3D_PM_REQUIRED_DOMAINS,
    _card3d_domains_for_task,
    _contains_prompt_leakage,
    _has_executable_or_file_acceptance_anchor,
    _has_measurable_acceptance_anchor,
    _has_placeholder_or_manifest_only_acceptance,
    _is_card3d_pm_contract,
    _normalize_path,
    _normalize_path_list,
    _normalize_text,
    _path_matches_card3d_domain,
    _strip_wrapping_quotes,
    _title_is_too_short,
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


class TestTitleQuality:
    def test_cjk_delivery_title_is_not_too_short(self) -> None:
        assert _title_is_too_short("实现匹配队列") is False
        assert _title_is_too_short("实现实时网关") is False
        assert _title_is_too_short("x") is True


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


class TestCard3dDomainMatching:
    def test_exact_client_scene_does_not_cover_sibling_client_domains(self) -> None:
        assert _path_matches_card3d_domain("src/client/three-scene.ts", "client3d") is True
        assert _path_matches_card3d_domain("src/client/three-scene.ts", "table") is False
        assert _path_matches_card3d_domain("src/client/three-scene.ts", "networking") is False

    def test_explicit_client_directory_scope_covers_client_card3d_domains(self) -> None:
        task = {"scope_paths": ["src/client"]}
        assert _card3d_domains_for_task(task, workspace_full=None) == ["client3d", "table", "networking"]

    def test_generic_server_and_tests_paths_do_not_identify_card3d_contract(self) -> None:
        payload = {
            "overall_goal": "Build a TypeScript REST API with tests.",
            "tasks": [
                {"id": "T1", "scope_paths": ["src/server"], "target_files": ["src/server/app.ts"]},
                {"id": "T2", "scope_paths": ["tests"], "target_files": ["tests/task-service.test.ts"]},
            ],
        }
        tasks = payload["tasks"]
        assert _is_card3d_pm_contract(payload, tasks) is False


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

    def test_directory_path_is_not_file_evidence_anchor(self) -> None:
        assert _has_executable_or_file_acceptance_anchor(["verify src/models exists"]) is False

    def test_status_only_is_not_anchor(self) -> None:
        assert _has_executable_or_file_acceptance_anchor(["page returns 200"]) is False


class TestHasPlaceholderOrManifestOnlyAcceptance:
    def test_placeholder_output_is_flagged(self) -> None:
        assert _has_placeholder_or_manifest_only_acceptance(["运行 npm start 能执行且不报错（占位输出即可）"]) is True
        assert _has_placeholder_or_manifest_only_acceptance(["placeholder output is ok for now"]) is True
        assert (
            _has_placeholder_or_manifest_only_acceptance(["src/main.ts 与 src/index.html 已创建（可为空或最小占位）"])
            is True
        )

    def test_manifest_only_is_flagged(self) -> None:
        assert _has_placeholder_or_manifest_only_acceptance(["npm test only checks package.json"]) is True
        assert _has_placeholder_or_manifest_only_acceptance(["package.json manifest-only script passes"]) is True

    def test_real_execution_acceptance_is_not_flagged(self) -> None:
        assert (
            _has_placeholder_or_manifest_only_acceptance(["`npm run test` validates the firefly dance rules"]) is False
        )
        assert (
            _has_placeholder_or_manifest_only_acceptance(["replace placeholder arithmetic tests with domain checks"])
            is False
        )


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

    def test_described_scope_paths_must_match_contract_paths(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "TASK-2",
                    "title": "Core domain model implementation",
                    "goal": "Implement firefly, flower, moon phase, and humidity domain rules",
                    "description": "Scope: `src/models/`, `src/engine/`; write the TypeScript domain model.",
                    "acceptance_criteria": ["`npm run test` exits 0", "verify src/models/index.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["write domain model", "run tests"],
                    "target_files": ["index.html", "tests/test_product.py"],
                    "scope_paths": ["index.html", "tests/test_product.py"],
                }
            ]
        }

        report = evaluate_pm_task_quality(payload)

        assert report["ok"] is False
        assert any(
            "described scope paths missing from target_files/scope_paths" in issue
            and "src/models" in issue
            and "src/engine" in issue
            for issue in report["critical_issues"]
        )

    def test_file_scope_paths_must_be_targeted_when_target_files_present(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Initialize simulation models",
                    "goal": "Implement Flower, Firefly, MoonPhase, and Garden TypeScript models.",
                    "description": "Create all core model files for the firefly garden simulator.",
                    "acceptance_criteria": ["`npm run build` exits 0", "verify src/models/MoonPhase.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write model files", "run build"],
                    "scope_paths": [
                        "package.json",
                        "tsconfig.json",
                        "src/models/Flower.ts",
                        "src/models/Firefly.ts",
                        "src/models/MoonPhase.ts",
                        "src/models/Garden.ts",
                    ],
                    "target_files": [
                        "package.json",
                        "tsconfig.json",
                        "src/models/Flower.ts",
                        "src/models/Firefly.ts",
                    ],
                }
            ]
        }

        report = evaluate_pm_task_quality(payload)

        assert report["ok"] is False
        assert any(
            "file-level scope_paths missing from target_files" in issue
            and "src/models/moonphase.ts" in issue
            and "src/models/garden.ts" in issue
            for issue in report["critical_issues"]
        )

    def test_first_product_delivery_task_cannot_be_documentation_only(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Detail design",
                    "goal": "Write design notes before implementing the TypeScript project.",
                    "description": "Define model rules for the firefly garden simulator.",
                    "acceptance_criteria": ["verify docs/design.md exists"],
                    "assigned_to": "director",
                    "phase": "requirements",
                    "depends_on": [],
                    "execution_checklist": ["write design document", "review the design"],
                    "scope_paths": ["docs/design.md"],
                    "target_files": ["docs/design.md"],
                },
                {
                    "id": "TASK-2",
                    "title": "Implement TypeScript project",
                    "goal": "Create package.json, tsconfig.json, and source files.",
                    "description": "Deliver runnable code for the product.",
                    "acceptance_criteria": ["`npm run build` exits 0", "verify package.json exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["write code", "run build"],
                    "scope_paths": ["package.json", "tsconfig.json", "src/main.ts"],
                    "target_files": ["package.json", "tsconfig.json", "src/main.ts"],
                },
            ]
        }

        report = evaluate_pm_task_quality(payload)

        assert report["ok"] is False
        assert any(
            "first product-delivery Director task cannot be documentation-only" in issue
            for issue in report["critical_issues"]
        )

    def test_single_documentation_delivery_task_is_allowed(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Write README",
                    "goal": "Document how to run the project.",
                    "description": "Produce a documentation-only deliverable.",
                    "acceptance_criteria": ["verify README.md exists"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": [],
                    "execution_checklist": ["write README", "review README"],
                    "scope_paths": ["README.md"],
                    "target_files": ["README.md"],
                }
            ]
        }

        report = evaluate_pm_task_quality(payload)

        assert not any(
            "first product-delivery Director task cannot be documentation-only" in issue
            for issue in report["critical_issues"]
        )

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

    def test_placeholder_acceptance_is_critical(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Setup real runtime",
                    "goal": "Initialize the project with real runnable scripts",
                    "acceptance_criteria": ["运行 npm start 能执行且不报错（占位输出即可）"],
                    "assigned_to": "director",
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": ["create package.json", "write src/main.ts"],
                    "scope_paths": ["package.json"],
                }
            ]
        }
        report = evaluate_pm_task_quality(payload)
        assert any("placeholder or manifest-only execution" in i for i in report["critical_issues"])

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
        assert any("game PM decomposition requires at least 12 tasks" in i for i in report["critical_issues"])
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


