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


class TestAutofixPmContractForQuality:
    @pytest.fixture(autouse=True)
    def _enable_domain_text_hints(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # These tests exercise the keyword-driven domain expansion CAPABILITY,
        # which is opt-in (default off per CLAUDE.md §8 after the phantom-task
        # incident). Production keeps the gate closed.
        monkeypatch.setenv("KERNELONE_PM_DOMAIN_TEXT_HINTS", "1")

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

    def test_split_director_task_boundaries_do_not_inherit_broad_contract_text(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: javascript；实现一个 npm CLI 产品",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "实现 npm 项目骨架与核心模块",
                    "goal": "交付 npm 项目、源代码、入口、测试和 README。",
                    "description": "Implement package, src modules, entrypoint, tests, and docs.",
                    "assigned_to": "director",
                    "phase": "implementation",
                    "target_files": [
                        "package.json",
                        "src/engine/rules.js",
                        "src/engine/runner.js",
                        "src/models/meteor.js",
                        "src/models/wish.js",
                        "src/models/queue.js",
                        "src/models/priority.js",
                        "src/index.js",
                        "tests/smoke.test.js",
                        "README.md",
                    ],
                    "scope": "package.json, src/index.js, src/engine, src/models, tests, README.md",
                    "scope_paths": [
                        "package.json",
                        "src/engine/rules.js",
                        "src/engine/runner.js",
                        "src/models/meteor.js",
                        "src/models/wish.js",
                        "src/models/queue.js",
                        "src/models/priority.js",
                        "src/index.js",
                        "tests/smoke.test.js",
                        "README.md",
                    ],
                    "steps": [
                        "创建 package.json",
                        "实现 src/index.js",
                        "实现 src/models/meteor.js 与 src/models/priority.js",
                        "执行 npm test",
                    ],
                    "acceptance": [
                        "`package.json`、`src/index.js`、`src/models/`、`tests/` 和 README 均存在",
                        "`npm run build`、`npm test` 与 `npm start` 全部通过",
                    ],
                    "acceptance_criteria": [
                        "`package.json`、`src/index.js`、`src/models/`、`tests/` 和 README 均存在",
                        "`npm run build`、`npm test` 与 `npm start` 全部通过",
                    ],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        assert stats["oversized_director_tasks_split"] == 1
        assert stats["task_boundary_tasks_added"] == 5
        foundation = payload["tasks"][0]
        foundation_blob = json.dumps(foundation, ensure_ascii=False).lower()
        assert foundation["target_files"] == ["package.json"]
        assert foundation["scope"] == "package.json"
        assert foundation["steps"] == [
            "Create or update only the listed project manifest/config files: package.json.",
            "Keep package/build/test/start script definitions internally consistent with downstream source and test tasks.",
            "Do not materialize downstream source, test, documentation, or model files in this boundary.",
        ]
        assert "src/index.js" not in foundation_blob
        assert "src/models" not in foundation_blob
        assert "tests/smoke.test.js" not in foundation_blob
        assert "npm test" not in foundation_blob

        source_models = next(task for task in payload["tasks"] if task["metadata"]["boundary_kind"] == "source_models")
        source_blob = json.dumps(source_models, ensure_ascii=False).lower()
        assert "package.json" not in source_blob
        assert "tests/smoke.test.js" not in source_blob
        assert "readme.md" not in source_blob
        assert "src/models/priority.js" in source_blob
        assert source_models["metadata"]["boundary_target_files"] == [
            "src/models/meteor.js",
            "src/models/wish.js",
            "src/models/queue.js",
            "src/models/priority.js",
        ]

        source_core = next(task for task in payload["tasks"] if task["metadata"]["boundary_kind"] == "source_core")
        source_core_blob = json.dumps(source_core, ensure_ascii=False).lower()
        assert "src/engine/rules.js" in source_core_blob
        assert "src/models/meteor.js" not in source_core["target_files"]

    def test_flags_duplicate_javascript_domain_source_layouts(self) -> None:
        payload: dict[str, Any] = {
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Implement JavaScript meteor module",
                    "goal": "Create the canonical meteor owner module.",
                    "assigned_to": "director",
                    "phase": "implementation",
                    "target_files": ["src/meteor.js", "src/models/meteor.js"],
                    "scope_paths": ["src/meteor.js", "src/models/meteor.js"],
                    "execution_checklist": ["Create source files", "Run npm test"],
                    "acceptance_criteria": ["verify src/meteor.js exists"],
                }
            ]
        }

        report = evaluate_pm_task_quality(payload, workspace_full="/fake")

        assert any("duplicate_domain_source_path" in item for item in report["critical_issues"])

    def test_hardens_existing_acceptance_with_file_evidence(self) -> None:
        payload = {
            "tasks": [
                {
                    "id": "PM-0001-1",
                    "title": "项目基础架构初始化",
                    "goal": "建立 Node.js/TypeScript 项目基础结构，包括依赖管理、TS 配置及目录骨架。",
                    "acceptance": [
                        "package.json 包含基础依赖及 scripts",
                        "tsconfig.json 配置正确且可编译",
                    ],
                    "assigned_to": "director",
                    "target_files": ["package.json", "tsconfig.json", "src/server/index.ts"],
                    "scope_mode": "module",
                    "scope_paths": ["src/"],
                }
            ]
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        report = evaluate_pm_task_quality(payload, workspace_full="/fake")

        assert stats["acceptance_hardened"] == 1
        assert "verify ./package.json exists" in payload["tasks"][0]["acceptance_criteria"]
        assert not any("requires executable command or file evidence" in item for item in report["critical_issues"])

    def test_hardens_directory_acceptance_with_representative_file_evidence(self, tmp_path: Any) -> None:
        models_dir = tmp_path / "src" / "models"
        repositories_dir = tmp_path / "src" / "repositories"
        models_dir.mkdir(parents=True)
        repositories_dir.mkdir(parents=True)
        (models_dir / "task.ts").write_text("export interface Task { id: string }\n", encoding="utf-8")
        (repositories_dir / "task-repository.ts").write_text("export class TaskRepository {}\n", encoding="utf-8")
        payload = {
            "tasks": [
                {
                    "id": "PM-0001-1",
                    "title": "设计并实现多租户隔离数据模型",
                    "goal": "建立带 tenant_id 的核心实体模型和租户隔离仓储边界。",
                    "acceptance_criteria": [
                        "验证租户 A 无法通过 API 查询到租户 B 的任何数据",
                        "verify src/models exists",
                    ],
                    "assigned_to": "director",
                    "target_files": [],
                    "scope_mode": "module",
                    "scope_paths": ["src/models", "src/repositories"],
                }
            ]
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))
        report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))

        assert stats["acceptance_hardened"] == 1
        assert "verify src/models/task.ts exists" in payload["tasks"][0]["acceptance_criteria"]
        assert not any("requires executable command or file evidence" in item for item in report["critical_issues"])

    def test_sanitizes_go_contract_respects_root_go_layout_without_pet_template(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；实现使用 root-level models 与 engine 包的 Go 模块",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "实现 Go 模块与 root-level 包布局",
                    "goal": "实现 root-level models 与 engine 包，不引入 src 镜像目录。",
                    "acceptance_criteria": [
                        "`go.mod` 与 `models/*.go` 存在且非空",
                        "`go test ./...` 返回成功",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [
                        "编写 models/entity.go",
                        "编写 engine/service.go",
                        "执行 go test ./...",
                    ],
                    "scope_paths": ["models/entity.go", "engine/service.go"],
                    "target_files": ["models/entity.go", "engine/service.go"],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        serialized = json.dumps(payload, ensure_ascii=False)

        assert stats["language_contract_paths_sanitized"] >= 1
        assert "src/models/pet.go" not in serialized
        assert "src/engine/engine.go" not in serialized
        assert payload["tasks"][0]["target_files"] == [
            "models/entity.go",
            "engine/service.go",
            "go.mod",
        ]

    def test_sanitizes_go_contract_uses_neutral_representatives_for_directory_only_scopes(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；实现一个包含模型层与引擎层的 Go 模块",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "实现 root-level Go 包",
                    "goal": "创建 models 与 engine 包。",
                    "acceptance_criteria": ["go test ./... passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["编写 models 包", "编写 engine 包"],
                    "scope_paths": ["models", "engine"],
                    "target_files": [],
                },
                {
                    "id": "TASK-2",
                    "title": "实现 src Go 包",
                    "goal": "创建 src/models 与 src/engine 包。",
                    "acceptance_criteria": ["go test ./... passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["编写 src/models 包", "编写 src/engine 包"],
                    "scope_paths": ["src/models", "src/engine"],
                    "target_files": [],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        serialized = json.dumps(payload, ensure_ascii=False)

        assert stats["language_contract_paths_sanitized"] >= 1
        assert "pet.go" not in serialized
        assert payload["tasks"][0]["target_files"] == ["models/model.go", "engine/engine.go"]
        assert payload["tasks"][1]["target_files"] == ["src/models/model.go", "src/engine/engine.go"]

    def test_sanitizes_go_contract_directory_evidence_without_typescript_drift(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "用 Go 实现 ASCII 魔法宠物终端，终端宠物学习咒语并用文本动画反馈情绪",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Go 项目骨架与模型层实现",
                    "goal": "实现 pet/spell/mood/ascii 模型层",
                    "acceptance_criteria": [
                        "go.mod 存在且 go build ./... 无错误",
                        "verify src/models/index.ts exists",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["编写 src/models/pet.go", "执行 go test ./..."],
                    "scope_paths": [
                        "src/models",
                        "src/models/pet.go",
                        "src/models/spell.go",
                        "src/models/models_test.go",
                    ],
                    "target_files": [],
                },
                {
                    "id": "TASK-2",
                    "title": "应用入口与 CLI 交互实现",
                    "goal": "创建 Go main.go 入口",
                    "acceptance_criteria": [
                        "main.go 存在且 go build 成功",
                        "verify src/cli/index.ts exists",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["创建 main.go", "执行 go build -o ascii-pet-terminal ."],
                    "scope_paths": ["src/cli", "tests"],
                    "target_files": [],
                },
                {
                    "id": "TASK-3",
                    "title": "实现QA 闭环与确定性检查验收",
                    "goal": "执行 go_compile 和入口 smoke",
                    "acceptance_criteria": ["go test ./... 全部通过"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "execution_checklist": ["执行 go test ./..."],
                    "scope_paths": ["README.md", "tests/test_ascii.py", "src/models/pet.go"],
                    "target_files": ["README.md", "tests/test_ascii.py"],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")
        serialized = str(payload)

        assert stats["language_contract_paths_sanitized"] >= 1
        assert "index.ts" not in serialized
        assert "tests/test_ascii.py" not in serialized
        assert payload["tasks"][0]["target_files"] == [
            "src/models/pet.go",
            "src/models/spell.go",
            "src/models/models_test.go",
            "go.mod",
            "src/models/model.go",
        ]
        assert payload["tasks"][1]["target_files"] == ["main.go"]
        assert "verify main.go exists" in payload["tasks"][1]["acceptance_criteria"]
        assert payload["tasks"][2]["target_files"] == ["README.md", "src/models/pet.go"]

    def test_sanitizes_go_contract_infers_entrypoint_module_and_qa_script_from_task_text(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；用 Go 实现 ASCII 魔法宠物终端",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Project Bootstrap & Domain Model Foundation",
                    "goal": "Create the Go module and bootstrap pet/spell/mood/ascii models.",
                    "acceptance_criteria": ["go.mod exists and go test ./... passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write go.mod", "write src/models/pet.go"],
                    "scope_paths": ["src/models"],
                    "target_files": [],
                },
                {
                    "id": "TASK-2",
                    "title": "CLI Entrypoint & Interactive Terminal Loop",
                    "goal": "Deliver a runnable CLI terminal entrypoint.",
                    "acceptance_criteria": ["main.go can be executed"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["write main.go"],
                    "scope_paths": [],
                    "target_files": [],
                },
                {
                    "id": "TASK-3",
                    "title": "QA Gate & Final Acceptance",
                    "goal": "Run QA validation script and sign off.",
                    "acceptance_criteria": ["scripts/qa.sh exits 0"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["TASK-2"],
                    "execution_checklist": ["write scripts/qa.sh", "execute the QA script"],
                    "scope_paths": ["scripts/qa.sh"],
                    "target_files": [],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        assert stats["language_contract_paths_sanitized"] >= 1
        assert payload["tasks"][0]["target_files"] == ["src/models/pet.go", "go.mod"]
        assert payload["tasks"][1]["target_files"] == ["main.go"]
        assert payload["tasks"][2]["target_files"] == ["scripts/qa.sh"]

    def test_sanitizes_go_contract_file_as_directory_paths(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；用 Go 实现 ASCII 魔法宠物终端",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Project Bootstrap & Domain Model Scaffold",
                    "goal": "Create Go pet, spell, mood, ascii models.",
                    "description": "Initialize src/models domain files for the Go terminal project.",
                    "acceptance_criteria": ["verify src/models/pet.go/index.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["write src/models/pet.go/index.ts"],
                    "scope_paths": ["src/models/pet.go/index.ts"],
                    "target_files": ["src/models/pet.go/index.ts", "src/models/spell.go"],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        serialized = json.dumps(payload, ensure_ascii=False)
        assert stats["language_contract_paths_sanitized"] >= 1
        assert "src/models/pet.go/index.ts" not in serialized
        assert "verify src/models/pet.go exists" in payload["tasks"][0]["acceptance_criteria"]
        assert payload["tasks"][0]["target_files"] == [
            "src/models/pet.go",
            "src/models/spell.go",
            "go.mod",
            "main.go",
        ]

    def test_sanitizes_go_contract_removes_web_python_ui_drift(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；用 Go 实现 ASCII 魔法宠物终端",
            "tasks": [
                {
                    "id": "TASK-3",
                    "title": "CLI入口与可执行主程序实现",
                    "goal": "提供可直接运行的CLI入口main.go，实现终端交互循环，验证入口可运行性",
                    "description": (
                        "构建main.go作为CLI可执行入口，集成models与engine包。 "
                        "[quality-gate] 禁止单文件大产物：HTML 只保留结构，样式写入 style.css、"
                        "逻辑写入 app.js（每个文件 ≤150 行）。单文件大写入会被输出预算截断且无法收敛。"
                    ),
                    "acceptance_criteria": [
                        "main.go存在且go run main.go可正常启动",
                        "tests/test_ascii.py uses unittest",
                        "index.html references style.css and app.js",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["TASK-2"],
                    "execution_checklist": [
                        "实现main.go",
                        "实现src/cmd/runner.go",
                        "编写tests/test_ascii.py",
                        "更新index.html和style.css",
                    ],
                    "scope_paths": ["index.html", "tests/test_ascii.py", "src/cmd/runner.go", "style.css"],
                    "target_files": ["index.html", "src/cmd/runner.go", "main.go", "style.css", "tests/test_ascii.py"],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        serialized = json.dumps(payload, ensure_ascii=False)
        assert stats["language_contract_paths_sanitized"] >= 1
        assert stats["single_file_ui_tasks_steered"] == 0
        assert "index.html" not in serialized
        assert "style.css" not in serialized
        assert "app.js" not in serialized
        assert "tests/test_ascii.py" not in serialized
        assert "HTML 只保留结构" not in serialized
        target_files = payload["tasks"][0]["target_files"]
        assert "src/cmd/runner.go" in target_files
        assert "main.go" in target_files
        assert all(not path.endswith((".html", ".css", ".js", ".py")) for path in target_files)

    def test_go_foundation_task_with_acceptance_text_still_infers_entrypoint_and_sources(self) -> None:
        payload: dict[str, Any] = {
            "overall_goal": "主语言: go；用 Go 实现 ASCII 魔法宠物终端",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "实现Go模块初始化与项目骨架",
                    "goal": "初始化Go模块，创建可编译运行的项目骨架，包含go.mod、main.go入口及README.md运行说明",
                    "description": "实现Go模块初始化与项目骨架，并满足验收标准。",
                    "acceptance_criteria": [
                        "go.mod 存在且执行 go mod tidy 无错误",
                        "main.go 存在且执行 go run main.go 输出非空字符串到终端",
                        "README.md 包含 go run main.go 命令示例及 pet、spell、mood、ascii 关键词",
                        "目录 src/models/ 和 src/engine/ 已创建",
                    ],
                    "assigned_to": "director",
                    "phase": "foundation",
                    "depends_on": [],
                    "execution_checklist": [
                        "执行 go mod init ascii-magic-pet 创建模块",
                        "编写 main.go 实现最小可运行CLI入口，导入本地包并调用核心函数",
                        "创建 src/models/、src/engine/ 目录结构",
                    ],
                    "scope_paths": ["README.md"],
                    "target_files": ["README.md"],
                }
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full="/fake")

        assert stats["language_contract_paths_sanitized"] >= 1
        assert payload["tasks"][0]["target_files"] == [
            "README.md",
            "go.mod",
            "main.go",
            "src/models/model.go",
            "src/engine/engine.go",
        ]

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

    def test_adds_final_cleanup_task_for_deterministic_scaffold_residue(self, tmp_path: Any) -> None:
        seed_file = tmp_path / "src" / "server" / "app.ts"
        seed_file.parent.mkdir(parents=True)
        seed_file.write_text(
            'export const marker = "audit-seed";\nexport const title = "planning scenario";\n',
            encoding="utf-8",
        )
        script_file = tmp_path / "scripts" / "test.mjs"
        script_file.parent.mkdir(parents=True)
        script_file.write_text(
            "console.log('test verification completed: 1 files');\n",
            encoding="utf-8",
        )
        payload: dict[str, Any] = {
            "tasks": [
                {
                    "id": "T01",
                    "title": "Implement runtime service",
                    "goal": "Replace generated runtime service with production behavior.",
                    "phase": "implementation",
                    "depends_on": [],
                    "target_files": ["src/server/app.ts"],
                    "scope_paths": ["src/server"],
                    "acceptance_criteria": ["Run `npm run test -- --watch=false` exits 0"],
                    "execution_checklist": ["Implement runtime service"],
                    "assigned_to": "director",
                }
            ]
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        cleanup_tasks = [
            task
            for task in payload["tasks"]
            if isinstance(task, dict)
            and task.get("metadata", {}).get("autofix_reason") == "deterministic_scaffold_residue_cleanup"
        ]
        assert stats["seed_residue_cleanup_tasks_added"] == 1
        assert len(cleanup_tasks) == 1
        cleanup = cleanup_tasks[0]
        assert cleanup["target_files"] == ["scripts/test.mjs", "src/server/app.ts"]
        assert cleanup["depends_on"] == ["T01"]
        acceptance = "\n".join(cleanup["acceptance_criteria"])
        assert "audit-seed" in acceptance
        assert "deterministic scaffold markers" in acceptance

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
            "overall_goal": (
                "Build a tactical roguelike game with combat, AI, content, progression, economy, "
                "audio, tooling, renderer, persistence, and tests."
            ),
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
                    "acceptance_criteria": ["verify src/world/procedural-map.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-engine"],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/world/procedural-map.ts"],
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

        assert stats["game_domain_tasks_added"] == 9
        assert len(payload["tasks"]) == 12
        target_files: set[str] = set()
        for task in payload["tasks"]:
            if not isinstance(task, dict):
                continue
            for path in task.get("target_files", []):
                if isinstance(path, str):
                    target_files.add(path)
        assert "src/ai/director-ai.ts" in target_files
        assert "src/content/cards.ts" in target_files
        assert "src/progression/campaign.ts" in target_files
        assert "src/economy/loot-table.ts" in target_files
        assert "src/persistence/save-system.ts" in target_files
        assert "src/renderer/scene-view.ts" in target_files
        assert "src/audio/sound-events.ts" in target_files
        assert "src/tools/balance-report.ts" in target_files
        assert "tests/integration/game-session.test.ts" in target_files

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True
        assert not any("game PM decomposition" in item for item in report["critical_issues"])

    def test_renderer_and_tests_paths_do_not_trigger_game_domain_tasks(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": "Build a glowing firefly garden simulator with TypeScript visuals and tests.",
            "tasks": [
                {
                    "id": "T01-renderer",
                    "title": "Implement firefly renderer",
                    "goal": "Render firefly, flower, moon, and humidity signals for the garden simulator.",
                    "acceptance_criteria": ["Run `npm run build` passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Implement", "Verify"],
                    "scope_paths": ["src/renderer/canvas-renderer.ts"],
                    "target_files": ["src/renderer/canvas-renderer.ts"],
                },
                {
                    "id": "T02-tests",
                    "title": "Add simulator tests",
                    "goal": "Verify the firefly garden simulation rules.",
                    "acceptance_criteria": ["Run `npm run test` passes"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["T01-renderer"],
                    "execution_checklist": ["Implement", "Verify"],
                    "scope_paths": ["tests/garden-simulation.test.ts"],
                    "target_files": ["tests/garden-simulation.test.ts"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert not any("game PM decomposition" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_domain_tasks_added"] == 0
        assert not any(
            isinstance(task, dict) and str(task.get("id") or "").startswith("PM-AUTO-") for task in payload["tasks"]
        )

    def test_adds_missing_card3d_domain_tasks_without_roguelike_repair(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": (
                "Build a multiplayer online creative card game with a TypeScript Three.js 3D client "
                "and a Node.js backend for realtime rooms, matchmaking, deck building, and sync."
            ),
            "tasks": [
                {
                    "id": "CARD3D-CLIENT",
                    "title": "Implement Three.js card scene",
                    "goal": "Implement the browser Three.js scene for the multiplayer creative card table.",
                    "acceptance_criteria": ["verify src/client/three-scene.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/client/three-scene.ts"],
                },
                {
                    "id": "CARD3D-SERVER",
                    "title": "Implement Node backend",
                    "goal": "Implement the Node.js backend entrypoint for multiplayer card sessions.",
                    "acceptance_criteria": ["verify src/server/app.ts exists"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["CARD3D-CLIENT"],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/server/app.ts"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert any("card3d PM decomposition missing domains" in item for item in initial_report["critical_issues"])
        assert not any("game PM decomposition" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["card3d_domain_tasks_added"] == len(_CARD3D_PM_REQUIRED_DOMAINS) - 2
        assert stats["game_domain_tasks_added"] == 0
        target_files: set[str] = set()
        for task in payload["tasks"]:
            if not isinstance(task, dict):
                continue
            for path in task.get("scope_paths", []):
                if isinstance(path, str):
                    target_files.add(path)
            for path in task.get("target_files", []):
                if isinstance(path, str):
                    target_files.add(path)
        expected_target_files = {
            "src/client/three-scene.ts",
            "src/client/card-table.ts",
            "src/client/network-client.ts",
            "src/server/app.ts",
            "src/server/realtime-gateway.ts",
            "src/server/matchmaking.ts",
            "src/server/room-state.ts",
            "src/game/card-catalog.ts",
            "src/game/deck-builder.ts",
            "src/game/rules-engine.ts",
            "src/shared/protocol.ts",
            "src/server/session-store.ts",
            "src/server/moderation.ts",
            "src/shared/player-presence.ts",
            "src/shared/telemetry.ts",
            "src/auth/session-auth.ts",
            "src/lobby/lobby-service.ts",
            "src/assets/card-assets.ts",
            "src/animation/card-animations.ts",
            "src/physics/table-layout.ts",
            "src/analytics/match-analytics.ts",
            "tests/unit/card-rules.test.ts",
            "tests/unit/deck-builder.test.ts",
            "tests/integration/multiplayer-flow.test.ts",
            "tests/integration/realtime-sync.test.ts",
            "tests/e2e/card-table-3d.test.ts",
        }
        assert expected_target_files.issubset(target_files)
        assert "src/world/procedural-map.ts" not in target_files
        assert "src/combat/combat-system.ts" not in target_files

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True
        assert not any("card3d PM decomposition" in item for item in report["critical_issues"])
        assert not any("game PM decomposition" in item for item in report["critical_issues"])

    def test_card3d_domain_coverage_ignores_context_files(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        task = {
            "id": "CARD3D-REALTIME",
            "title": "Build realtime gateway",
            "goal": "Implement room-scoped realtime messages for the Card3D table.",
            "scope_paths": ["src/server/realtime-gateway.ts"],
            "target_files": ["src/server/realtime-gateway.ts"],
            "context_files": [
                "src/server/app.ts",
                "src/server/session-store.ts",
                "src/auth/session-auth.ts",
                "src/client/three-scene.ts",
            ],
        }

        assert _card3d_domains_for_task(task, workspace) == ["realtime"]

    def test_card3d_tasks_with_exact_targets_survive_broad_scope_paths(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": (
                "Build a multiplayer online creative card game with a TypeScript Three.js 3D client "
                "and a Node.js backend for realtime rooms, matchmaking, deck building, and sync."
            ),
            "tasks": [
                {
                    "id": "PM-0001-1",
                    "title": "Implement Card3D client table",
                    "goal": "Implement the browser Three.js card scene, card table, and network client.",
                    "target_files": [
                        "src/client/three-scene.ts",
                        "src/client/card-table.ts",
                        "src/client/network-client.ts",
                    ],
                    "scope_paths": ["src"],
                    "acceptance_criteria": [
                        "Run `npm run build` exits 0.",
                        "Files src/client/three-scene.ts and src/client/card-table.ts exist.",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [
                        "Read existing client files and shared protocol.",
                        "Implement the 3D card table rendering and interaction flow.",
                        "Run npm run build and record the result.",
                    ],
                },
                {
                    "id": "PM-0002-1",
                    "title": "Implement Card3D realtime server",
                    "goal": "Implement backend app, realtime gateway, matchmaking, and room state.",
                    "target_files": [
                        "src/server/app.ts",
                        "src/server/realtime-gateway.ts",
                        "src/server/matchmaking.ts",
                        "src/server/room-state.ts",
                    ],
                    "scope_paths": ["src"],
                    "acceptance_criteria": [
                        "Run `npm run build` exits 0.",
                        "Files src/server/app.ts and src/server/room-state.ts exist.",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["PM-0001-1"],
                    "execution_checklist": [
                        "Read client network contract.",
                        "Implement realtime room lifecycle and matchmaking.",
                        "Run npm run build and record the result.",
                    ],
                },
                {
                    "id": "PM-0003-1",
                    "title": "Implement Card3D game model and tests",
                    "goal": "Implement card catalog, deck builder, rules engine, protocol, session store, moderation, and tests.",
                    "target_files": [
                        "src/game/card-catalog.ts",
                        "src/game/deck-builder.ts",
                        "src/game/rules-engine.ts",
                        "src/shared/protocol.ts",
                        "src/server/session-store.ts",
                        "src/server/moderation.ts",
                        "src/shared/player-presence.ts",
                        "src/shared/telemetry.ts",
                        "src/auth/session-auth.ts",
                        "src/lobby/lobby-service.ts",
                        "src/assets/card-assets.ts",
                        "src/animation/card-animations.ts",
                        "src/physics/table-layout.ts",
                        "src/analytics/match-analytics.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                    ],
                    "scope_paths": ["src", "tests"],
                    "acceptance_criteria": [
                        "Run `npm run build` exits 0.",
                        "Run `npm test` exits 0.",
                    ],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["PM-0002-1"],
                    "execution_checklist": [
                        "Read server and client contracts.",
                        "Implement game rules, persistence-facing session behavior, and integration test.",
                        "Run npm run build and npm test.",
                    ],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_policy_tasks_removed"] == 0
        assert stats["card3d_domain_tasks_added"] == 0
        assert stats["card3d_test_contract_repairs"] > 0
        remaining_ids = {task["id"] for task in payload["tasks"] if isinstance(task, dict)}
        assert {"PM-0001-1", "PM-0002-1", "PM-0003-1"}.issubset(remaining_ids)
        all_targets = {
            target for task in payload["tasks"] if isinstance(task, dict) for target in task.get("target_files", [])
        }
        assert "scripts/build.mjs" in all_targets
        assert "scripts/test.mjs" in all_targets

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert any("card3d PM decomposition requires at least" in item for item in report["critical_issues"])
        assert not any(
            "card3d tests task must target all required test files" in item for item in report["critical_issues"]
        )
        assert not any("unknown dependency" in item for item in report["critical_issues"])
        assert not any("stack mutation" in item.lower() for item in report["critical_issues"])

    def test_card3d_tests_task_requires_all_seed_test_targets(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": (
                "Build a multiplayer online creative card game with a TypeScript Three.js 3D client "
                "and a Node.js backend."
            ),
            "tasks": [
                {
                    "id": "PM-CARD3D-TESTS",
                    "title": "Add multiplayer card integration tests",
                    "goal": "Replace placeholder tests with meaningful multiplayer card coverage.",
                    "target_files": ["tests/integration/multiplayer-flow.test.ts"],
                    "scope_paths": ["tests"],
                    "acceptance_criteria": ["Run `npm test` exits 0."],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [
                        "Read all seed test files.",
                        "Replace placeholder arithmetic tests.",
                        "Run npm test.",
                    ],
                }
            ],
        }

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)

        assert any(
            "card3d tests task must target all required test files" in item for item in report["critical_issues"]
        )
        assert "tests/unit/card-rules.test.ts" in "\n".join(report["critical_issues"])
        assert "scripts/build.mjs" in "\n".join(report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)
        repaired_targets = payload["tasks"][0]["target_files"]

        assert stats["card3d_test_contract_repairs"] > 0
        assert "scripts/build.mjs" in repaired_targets
        assert "scripts/test.mjs" in repaired_targets

    def test_card3d_tests_task_requires_placeholder_cleanup_contract(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": (
                "Build a multiplayer online creative card game with a TypeScript Three.js 3D client "
                "and a Node.js backend."
            ),
            "tasks": [
                {
                    "id": "PM-CARD3D-TESTS",
                    "title": "Add multiplayer card integration tests",
                    "goal": "Add meaningful multiplayer card coverage.",
                    "target_files": [
                        "scripts/build.mjs",
                        "scripts/test.mjs",
                        "tests/unit/card-rules.test.ts",
                        "tests/unit/deck-builder.test.ts",
                        "tests/integration/multiplayer-flow.test.ts",
                        "tests/integration/realtime-sync.test.ts",
                        "tests/e2e/card-table-3d.test.ts",
                    ],
                    "scope_paths": ["tests"],
                    "acceptance_criteria": ["Run `npm test` exits 0."],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [
                        "Read all seed test files.",
                        "Add multiplayer coverage.",
                        "Run npm test.",
                    ],
                }
            ],
        }

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)

        assert any(
            "card3d tests task must require replacing/removing trivial arithmetic placeholder tests" in item
            for item in report["critical_issues"]
        )

    def test_card3d_autofix_removes_unanchored_requirements_fallback_tasks(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": "Build Typescript api from requirements.",
            "focus": "Recover from PM invalid output and continue delivery safely.",
            "tasks": [
                {
                    "id": "PM-0001-F1",
                    "title": "Requirements bootstrap (Typescript Api)",
                    "goal": "Create initial project files derived from requirements. Use Typescript conventions.",
                    "target_files": [
                        "package.json",
                        "Temp/docs/architect-plan.md",
                        "Temp/docs/chief-engineer-blueprint.md",
                        "Three.js",
                        "Node.js",
                    ],
                    "scope_paths": ["Temp/docs"],
                    "acceptance_criteria": [
                        "verify: Bootstrap target files are created and syntactically valid.",
                        "At least one verification command is runnable: npm test",
                    ],
                    "assigned_to": "Director",
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": ["Create bootstrap files", "Run npm test"],
                    "description": (
                        "Technology Stack: Typescript\nProject Type: Api\nRequirements Summary: "
                        "构建多人在线创意卡牌游戏，交付 TypeScript + Three.js 3D 牌桌客户端、"
                        "Node.js 后端、实时网关、匹配、房间状态、牌组构筑和同步协议。"
                    ),
                },
                {
                    "id": "PM-0001-F2",
                    "title": "Requirements implementation (Typescript Api)",
                    "goal": "Implement core module files derived from requirements. Use Typescript conventions.",
                    "target_files": ["Three.js", "Node.js"],
                    "scope_paths": ["Three.js", "Node.js"],
                    "acceptance_criteria": [
                        "verify: Core module files are implemented with coherent behavior.",
                        "Primary implementation file contains non-trivial business logic.",
                    ],
                    "assigned_to": "Director",
                    "phase": "implementation",
                    "depends_on": ["PM-0001-F1"],
                    "execution_checklist": ["Implement core module files", "Verify behavior"],
                    "description": (
                        "Technology Stack: Typescript\nProject Type: Api\nRequirements Summary: "
                        "多人在线创意卡牌游戏需要 Three.js 3D 牌桌、Node.js 后端、"
                        "WebSocket 实时同步、matchmaking、rooms、cards、rules 和 tests。"
                    ),
                },
                {
                    "id": "PM-0001-F3",
                    "title": "Requirements tests (Typescript Api)",
                    "goal": "Create or update tests derived from requirements. Use Typescript conventions.",
                    "target_files": ["tests/service.test.ts"],
                    "scope_paths": ["tests"],
                    "acceptance_criteria": [
                        "verify: At least one test file exists and validates core behavior.",
                        "Verification command passes: npm test",
                    ],
                    "assigned_to": "Director",
                    "phase": "verification",
                    "depends_on": ["PM-0001-F2"],
                    "execution_checklist": ["Create tests", "Run npm test"],
                    "description": "Validate the multiplayer creative card game realtime flow.",
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_policy_tasks_removed"] == 3
        assert stats["card3d_domain_tasks_added"] == len(_CARD3D_PM_REQUIRED_DOMAINS)
        remaining_ids = {task["id"] for task in payload["tasks"] if isinstance(task, dict)}
        assert remaining_ids.isdisjoint({"PM-0001-F1", "PM-0001-F2", "PM-0001-F3"})
        assert len(payload["tasks"]) == len(_CARD3D_PM_REQUIRED_DOMAINS)

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True
        assert not report["critical_issues"]

    def test_sanitizes_fragile_prng_acceptance(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": "Build a tactical roguelike game with deterministic PRNG systems.",
            "tasks": [
                {
                    "id": "T01-prng",
                    "title": "Implement deterministic PRNG",
                    "goal": "Implement deterministic game random number generation.",
                    "acceptance_criteria": [
                        "verify src/engine/prng.ts exists",
                        "1000次序列生成与参考序列逐位一致",
                        "卡方检验p>0.01",
                        "执行 npm test -- --testPathPattern prng 通过，验证快照序列",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read", "Implement", "Verify"],
                    "scope_paths": ["src/engine/prng.ts"],
                    "target_files": ["C:/Temp/src/engine/prng.ts"],
                    "backlog_ref": "Implement C:/Temp/src/engine/prng.ts without brittle snapshots or hard-coded expected values",
                }
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert any("fragile random-sequence assertions" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        task = payload["tasks"][0]
        acceptance = "\n".join(str(item) for item in task["acceptance_criteria"])
        assert stats["acceptance_sanitized"] == 1
        assert "参考序列" not in acceptance
        assert "卡方" not in acceptance
        assert "快照序列" not in acceptance
        assert "literal output snapshots" in acceptance
        assert task["target_files"] == ["src/engine/prng.ts"]
        assert "C:/Temp" not in str(task["backlog_ref"])

    def test_mixed_chinese_roguelike_goal_is_game_expanded(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": "构建可执行、可测试、可审计的中大型Web战术Roguelike游戏",
            "focus": "搭建项目基础并实现确定性PRNG引擎",
            "tasks": [
                {
                    "id": "T01-esm",
                    "title": "搭建项目骨架与ESM模块结构",
                    "goal": "创建package.json、TypeScript配置、基础源码与测试占位，确保npm build和test骨架可运行",
                    "acceptance_criteria": [
                        "npm install 成功无错误退出",
                        "npm run build 生成dist/目录，包含index.js",
                        "npm test 执行至少一个占位测试并通过",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": [
                        "Initialize package.json with devDependencies",
                        "安装TypeScript和测试运行器(如vitest)为devDependencies",
                        "Create build.mjs and test.mjs in the workspace root",
                    ],
                    "scope_paths": ["package.json", "src", "build.mjs", "test.mjs"],
                    "target_files": ["package.json", "src/index.ts", "test/index.test.ts", "build.mjs", "test.mjs"],
                },
                {
                    "id": "T01-prng",
                    "title": "实现确定性PRNG与种子一致性测试",
                    "goal": "实现xorshift PRNG，并通过1000步序列比对验证给定种子的确定性输出",
                    "acceptance_criteria": [
                        "执行 npm test -- --testPathPattern prng 通过，验证快照序列",
                        "PRNG模块导出的类能够从其他模块导入",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-esm"],
                    "execution_checklist": [
                        "在test/prng.test.ts编写测试",
                        "硬编码1000个预期值数组，断言deepStrictEqual",
                    ],
                    "scope_paths": ["src"],
                    "target_files": ["src/prng.ts", "test/prng.test.ts"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert any(
            "game PM decomposition requires at least 12 tasks" in item for item in initial_report["critical_issues"]
        )
        assert any("fragile random-sequence assertions" in item for item in initial_report["critical_issues"])
        assert any("no-external-dependency policy" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_policy_tasks_removed"] == 2
        assert stats["game_domain_tasks_added"] == 12
        assert len(payload["tasks"]) == 12
        assert {task["id"] for task in payload["tasks"] if isinstance(task, dict)}.isdisjoint({"T01-esm", "T01-prng"})
        assert all(
            "package.json" not in task.get("target_files", []) for task in payload["tasks"] if isinstance(task, dict)
        )
        serialized = str(payload["tasks"]).lower()
        assert "npm install" not in serialized
        assert "vitest" not in serialized
        assert "jest" not in serialized
        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True
        assert not any("game PM decomposition" in item for item in report["critical_issues"])

    def test_removes_legacy_narrow_game_contract_tasks_before_domain_repair(self, tmp_path: Any) -> None:
        workspace = str(tmp_path)
        payload: dict[str, Any] = {
            "workspace": workspace,
            "overall_goal": (
                "Build a tactical roguelike game with engine, world, combat, AI, content, progression, "
                "economy, persistence, renderer, audio, tooling, and tests while preserving existing scripts."
            ),
            "focus": "Bootstrap project files, then implement xorshift128+ PRNG, then map generation with A* connectivity.",
            "tasks": [
                {
                    "id": "T01-initialize_project_s",
                    "title": "Initialize project scaffold and build system",
                    "goal": (
                        "Create package.json, src structure, and test harness so that `npm run build` "
                        "and `npm test` execute successfully."
                    ),
                    "acceptance_criteria": [
                        "npm run build exits 0",
                        "npm test exits 0",
                        "package.json contains type: module and build/test scripts",
                    ],
                    "assigned_to": "director",
                    "phase": "bootstrap",
                    "depends_on": [],
                    "execution_checklist": [
                        "Create package.json",
                        "Create src/index.mjs as placeholder entry file",
                    ],
                    "scope_paths": ["src", "tests"],
                    "target_files": ["src/index.mjs", "tests/placeholder.test.mjs", "package.json"],
                },
                {
                    "id": "T01-implement_xorshift12",
                    "title": "Implement xorshift128+ PRNG engine with seed string hashing",
                    "goal": "Deliver a PRNG module exposing next(), nextRange(), getState(), setState().",
                    "acceptance_criteria": [
                        "Run npm test -- tests/prng.test.mjs exits 0",
                        "Same seed yields identical calling sequence",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-initialize_project_s"],
                    "execution_checklist": ["Implement src/prng.mjs", "Write tests/prng.test.mjs"],
                    "scope_paths": ["src", "tests"],
                    "target_files": ["src/prng.mjs", "tests/prng.test.mjs"],
                },
                {
                    "id": "T01-implement_procedural",
                    "title": "Implement procedural room placement with A* connectivity",
                    "goal": "Generate maps with rooms, corridors, and a single connected component.",
                    "acceptance_criteria": [
                        "Run npm test -- tests/map.test.mjs exits 0",
                        "Same seed generates identical serialized output",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-implement_xorshift12"],
                    "execution_checklist": ["Implement src/map.mjs", "Write tests/map.test.mjs"],
                    "scope_paths": ["src", "tests"],
                    "target_files": ["src/map.mjs", "tests/map.test.mjs"],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=workspace)

        assert stats["game_policy_tasks_removed"] == 3
        assert stats["game_domain_tasks_added"] == 12
        remaining_ids = {task["id"] for task in payload["tasks"] if isinstance(task, dict)}
        assert remaining_ids.isdisjoint(
            {
                "T01-initialize_project_s",
                "T01-implement_xorshift12",
                "T01-implement_procedural",
            }
        )
        serialized = str(payload["tasks"]).lower()
        assert "package.json contains" not in serialized
        assert "xorshift" not in serialized
        assert "src/map.mjs" not in serialized

        report = evaluate_pm_task_quality(payload, workspace_full=workspace)
        assert report["ok"] is True

    def test_workspace_plan_hints_expand_prng_only_pm_output(self, tmp_path: Any) -> None:
        plan_dir = tmp_path / "runtime" / "contracts"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text(
            "\n".join(
                [
                    "# Plan",
                    "- PRNG-001: Implement seedable xorshift128+",
                    "- MAP-001: Design hex grid coordinate system",
                    "- COM-001: Build action point economy",
                    "- AI-001: Design behavior tree parser",
                    "- MAP-002: Implement terrain generation and encounter placement",
                ]
            ),
            encoding="utf-8",
        )
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Establish project scaffolding and implement deterministic PRNG engine",
            "focus": "Bootstrap npm project and first core module",
            "tasks": [
                {
                    "id": "T01-prng",
                    "title": "Implement seedable PRNG module",
                    "goal": "Create deterministic PRNG with seed and range tests.",
                    "acceptance_criteria": ["Run `node src/prng/xorshift128.test.js` passes"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Create PRNG", "Run test"],
                    "scope_paths": ["src/prng"],
                    "target_files": ["src/prng/xorshift128.js", "src/prng/xorshift128.test.js"],
                }
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert not any("game PM decomposition" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["game_context_attached"] == 1
        assert stats["game_domain_tasks_added"] >= 10
        report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
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

    def test_autofix_splits_oversized_director_task_boundaries(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Build a TypeScript market simulation with models, CLI, web entry, and tests.",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Implement TypeScript market project",
                    "goal": "Create package, config, models, entries, and behavior tests for the market project.",
                    "description": "One broad task intentionally includes manifest, source, entrypoints, and tests.",
                    "acceptance_criteria": [
                        "`npm run build` exits 0",
                        "`npm run test` exits 0",
                        "`npm start` exits 0",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Create files", "Run tests"],
                    "scope_paths": [
                        "package.json",
                        "tsconfig.json",
                        "src/types.ts",
                        "src/models/Market.ts",
                        "src/models/Fairy.ts",
                        "src/models/Inventory.ts",
                        "src/index.ts",
                        "src/main.ts",
                        "src/web.ts",
                        "tests/behavior.test.ts",
                    ],
                    "target_files": [
                        "package.json",
                        "tsconfig.json",
                        "src/types.ts",
                        "src/models/Market.ts",
                        "src/models/Fairy.ts",
                        "src/models/Inventory.ts",
                        "src/index.ts",
                        "src/main.ts",
                        "src/web.ts",
                        "tests/behavior.test.ts",
                    ],
                },
                {
                    "id": "TASK-2",
                    "title": "Add README handoff",
                    "goal": "Document how to run the market simulation.",
                    "acceptance_criteria": ["verify README.md exists"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["Write README"],
                    "scope_paths": ["README.md"],
                    "target_files": ["README.md"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert any("Director task boundary is too broad" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["oversized_director_tasks_split"] == 1
        assert stats["task_boundary_tasks_added"] == 4
        split_tasks = [task for task in payload["tasks"] if str(task.get("id", "")).startswith("TASK-1-")]
        assert [task["id"] for task in split_tasks] == [
            "TASK-1-foundation",
            "TASK-1-source-models",
            "TASK-1-source-modules",
            "TASK-1-entrypoints",
            "TASK-1-tests",
        ]
        assert split_tasks[0]["target_files"] == ["package.json", "tsconfig.json"]
        assert "src/models/Market.ts".lower() in split_tasks[1]["target_files"]
        assert "src/types.ts".lower() in split_tasks[2]["target_files"]
        assert "src/main.ts".lower() in split_tasks[3]["target_files"]
        assert split_tasks[4]["target_files"] == ["tests/behavior.test.ts"]
        assert split_tasks[1]["depends_on"] == ["TASK-1-foundation"]
        assert split_tasks[2]["depends_on"] == ["TASK-1-source-models"]
        assert split_tasks[3]["depends_on"] == ["TASK-1-source-modules"]
        assert split_tasks[4]["depends_on"] == ["TASK-1-entrypoints"]
        downstream = next(task for task in payload["tasks"] if task["id"] == "TASK-2")
        assert downstream["depends_on"] == ["TASK-1-tests"]
        post_report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert not any("Director task boundary is too broad" in item for item in post_report["critical_issues"])

    def test_autofix_keeps_lightweight_l1_director_task_single_boundary(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Build a small TypeScript simulation toy.",
            "directive": "Bench Level Contract (Mandatory): level: 1",
            "tasks": [
                {
                    "id": "TASK-1",
                    "title": "Implement lightweight TypeScript simulation",
                    "goal": "Create package, config, models, and entrypoints for an L1 simulation.",
                    "description": "One lightweight L1 task includes manifest, source models, and entrypoints.",
                    "acceptance_criteria": [
                        "`npm run build` exits 0",
                        "`npm run test` exits 0",
                    ],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Create files", "Run tests"],
                    "scope_paths": [
                        "package.json",
                        "tsconfig.json",
                        "src/models/firefly.ts",
                        "src/models/flower.ts",
                        "src/models/moonphase.ts",
                        "src/models/humidity.ts",
                        "src/index.ts",
                        "src/main.ts",
                    ],
                    "target_files": [
                        "package.json",
                        "tsconfig.json",
                        "src/models/firefly.ts",
                        "src/models/flower.ts",
                        "src/models/moonphase.ts",
                        "src/models/humidity.ts",
                        "src/index.ts",
                        "src/main.ts",
                    ],
                },
                {
                    "id": "TASK-2",
                    "title": "Add tests",
                    "goal": "Create behavior tests for the simulation.",
                    "acceptance_criteria": ["verify tests/simulation.test.ts exists"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": ["TASK-1"],
                    "execution_checklist": ["Write tests"],
                    "scope_paths": ["tests/simulation.test.ts"],
                    "target_files": ["tests/simulation.test.ts"],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert not any("Director task boundary is too broad" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["oversized_director_tasks_split"] == 0
        assert stats["task_boundary_tasks_added"] == 0
        assert [task["id"] for task in payload["tasks"]] == ["TASK-1", "TASK-2"]

    def test_autofix_does_not_split_documentation_only_director_task(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "Create documentation handoff assets.",
            "tasks": [
                {
                    "id": "TASK-docs",
                    "title": "Write documentation handoff",
                    "goal": "Create all markdown handoff assets for the project.",
                    "acceptance_criteria": ["verify docs/overview.md exists"],
                    "assigned_to": "director",
                    "phase": "verification",
                    "depends_on": [],
                    "execution_checklist": ["Write documentation"],
                    "scope_paths": [
                        "README.md",
                        "docs/overview.md",
                        "docs/setup.md",
                        "docs/api.md",
                        "docs/usage.md",
                        "docs/testing.md",
                        "docs/release.md",
                    ],
                    "target_files": [
                        "README.md",
                        "docs/overview.md",
                        "docs/setup.md",
                        "docs/api.md",
                        "docs/usage.md",
                        "docs/testing.md",
                        "docs/release.md",
                    ],
                },
            ],
        }

        initial_report = evaluate_pm_task_quality(payload, workspace_full=str(tmp_path))
        assert not any("Director task boundary is too broad" in item for item in initial_report["critical_issues"])

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["oversized_director_tasks_split"] == 0
        assert stats["task_boundary_tasks_added"] == 0
        assert [task["id"] for task in payload["tasks"]] == ["TASK-docs"]

    def test_enterprise_task_management_terms_do_not_trigger_game_domain_expansion(self, tmp_path: Any) -> None:
        payload: dict[str, Any] = {
            "workspace": str(tmp_path),
            "overall_goal": "构建支持多租户隔离的企业级任务管理系统。",
            "focus": "Tenant lifecycle, RBAC, audit trail, task workflow, and reporting APIs.",
            "notes": (
                "Out of scope: AI/ML预测式任务调度优化；"
                "任务内容本身的业务逻辑实现（仅管理执行壳）；"
                "工具侧只覆盖管理、审计和交付验证。"
            ),
            "tasks": [
                {
                    "id": "T01-tenant-api",
                    "title": "Implement tenant lifecycle API",
                    "goal": "Implement tenant creation, suspension, and isolation checks.",
                    "description": "Deliver enterprise task management tenant boundaries.",
                    "acceptance_criteria": ["verify src/api/tenants.ts exists", "Run `npm test` exits 0"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": [],
                    "execution_checklist": ["Read tenant requirements", "Implement API", "Run tests"],
                    "scope_paths": ["src/api/tenants.ts"],
                    "target_files": ["src/api/tenants.ts", "tests/tenant-api.test.ts"],
                },
                {
                    "id": "T02-rbac-audit",
                    "title": "Implement RBAC audit workflow",
                    "goal": "Implement role checks and audit event persistence for task operations.",
                    "description": "工具 and content refer to enterprise workflow operations.",
                    "acceptance_criteria": ["verify src/security/rbac.ts exists", "Run `npm test` exits 0"],
                    "assigned_to": "director",
                    "phase": "implementation",
                    "depends_on": ["T01-tenant-api"],
                    "execution_checklist": ["Read RBAC contract", "Implement audit flow", "Run tests"],
                    "scope_paths": ["src/security/rbac.ts"],
                    "target_files": ["src/security/rbac.ts", "tests/rbac-audit.test.ts"],
                },
            ],
        }

        stats = autofix_pm_contract_for_quality(payload, workspace_full=str(tmp_path))

        assert stats["game_context_attached"] == 0
        assert stats["game_domain_tasks_added"] == 0
        assert len(payload["tasks"]) == 2
        assert not any(
            task.get("metadata", {}).get("autofix_reason") == "game_pm_domain_coverage"
            for task in payload["tasks"]
            if isinstance(task, dict)
        )

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
