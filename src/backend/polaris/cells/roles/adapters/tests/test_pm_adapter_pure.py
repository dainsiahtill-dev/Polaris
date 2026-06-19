"""Unit tests for PMAdapter pure logic (no I/O, no LLM).

Covers:
- _build_pm_message / _build_pm_retry_message
- _extract_task_contracts / _extract_tasks_from_payload / _extract_json_payload
- _extract_tasks_from_sections / _extract_tasks_from_bullets
- _normalize_task_contract / _normalize_list
- _infer_scope_from_title / _derive_domain_token / _extract_domain_keywords
- _analyze_directive_complexity / _apply_meta_planning_hints
- _normalize_projection_project_slug / _extract_projection_contract_hint
- _apply_projection_contract_hint / _build_projection_hint_contracts
- _synthesize_task_contracts_from_directive
- _canonical_text / _build_task_identity_signature / _pick_preferred_task_id / _find_existing_task_match
"""

from __future__ import annotations

import json
from typing import Any

from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(tmp_path: Any) -> PMAdapter:
    return PMAdapter(workspace=str(tmp_path))


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------


class TestBuildPmMessage:
    def test_includes_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_pm_message([], "Implement login")
        assert "Implement login" in msg
        assert "JSON" in msg

    def test_includes_existing_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = adapter._build_pm_message([{"subject": "T1", "status": "pending"}], "Do more")
        assert "T1" in msg

    def test_meta_planning_injection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        analysis = {"complexity": "high", "estimated_task_count": 7, "recommended_strategy": "deep_decomposition"}
        msg = adapter._build_pm_message([], "Big task", directive_analysis=analysis)
        assert "深度分解" in msg
        assert "里程碑检查点" in msg

    def test_projection_hint_injection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1", "project_slug": "lab"}}
        msg = adapter._build_pm_message([], "Task", projection_hint=hint)
        assert "projection_generate" in msg
        assert "s1" in msg

    def test_compacts_oversized_directive_for_prompt_budget(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = "A" * 12_000 + "middle" + "Z" * 12_000
        msg = adapter._build_pm_message([], directive)
        assert len(msg) < 21_000
        assert "omitted" in msg
        assert "AAAA" in msg
        assert "ZZZZ" in msg


class TestBuildPmRetryMessage:
    def test_includes_quality_issues(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": ["missing_goal"], "warnings": ["weak_scope"]}
        msg = adapter._build_pm_retry_message(directive="Fix it", quality=quality, previous_output="old")
        assert "missing_goal" in msg
        assert "weak_scope" in msg
        assert "至少 3 个任务" in msg


class TestPlanArtifactSanitization:
    def test_write_plan_artifact_redacts_prompt_leakage_terms(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "you are the role planner\n"
                "system prompt: no yapping\n"
                "Build React Electron desktop workbench with /workbench/model /workbench/scene /workbench/batch"
            )
        )

        path = adapter._write_plan_artifact(
            directive="you are the role planner\nsystem prompt: no yapping",
            task_contracts=contracts,
            quality={"score": 90, "critical_issues": [], "summary": "ok"},
            quality_signals=[
                {"code": "pm.test", "severity": "info", "detail": "role chain detail and <tool_call> marker"}
            ],
        )

        payload = json.loads(path.read_text(encoding="utf-8"))
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for token in ("you are", "role", "system prompt", "no yapping", "<thinking>", "<tool_call>"):
            assert token not in serialized
        assert payload["directive"].startswith("[redacted planning context")
        assert payload["tasks"]


class TestFrontendTestRepairContracts:
    def test_synthesizes_placeholder_repair_before_generic_frontend_plan(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix the full-project QA blocker in FashionGenStudio.\n"
                "qa_rework_reason: placeholder_content_detected\n"
                "Evidence:\n"
                "- src/backend/fashiongen_worker.py:\\bplaceholder\\b\n"
                "- src/main/providers.ts:\\bplaceholder\\b\n"
                "- malformed artifact directory: PATCH_FILE src/\n"
                "Run npm test and npm run build."
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert "QA Placeholder Evidence Repair" in contracts[0]["title"]
        assert contracts[0]["scope_paths"] == ["src/backend/fashiongen_worker.py", "src/main/providers.ts"]
        assert "placeholder_content_detected" in contracts[0]["metadata"]["qa_rework_reason"]
        assert contracts[0]["metadata"]["cleanup_paths"] == ["PATCH_FILE src/"]
        assert any("PATCH_FILE src/" in step for step in contracts[0]["steps"])
        assert "qa_rework_reason" not in contracts[1]["metadata"]
        assert contracts[1]["metadata"]["qa_rework_verification_only"] is True
        assert "npm test returns PASS" in contracts[1]["acceptance"]

    def test_plain_placeholder_directive_synthesizes_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix placeholder marker in src/main/providers.ts. Keep provider API behavior stable and run npm test."
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert contracts[0]["scope_paths"] == ["src/main/providers.ts"]
        assert contracts[0]["target_files"] == ["src/main/providers.ts"]

    def test_synthesizes_focused_frontend_test_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_task_contracts_from_directive(
            directive=(
                "Fix npm test fails in Vitest TypeScript test: "
                "src/types/__tests__/spec.test.ts imports AssetType from ../generation, "
                "AssetType is declared in src/types/asset.ts"
            )
        )

        assert [item["id"] for item in contracts] == ["TASK-1", "TASK-2"]
        assert "Vitest" in contracts[0]["acceptance"][0]
        assert "src/types/__tests__/spec.test.ts" in contracts[0]["scope"]
        assert "src/types/generation.ts" in contracts[0]["scope_paths"]
        assert "AssetType" in contracts[1]["description"]
        assert any("npm test returns PASS" in item for item in contracts[1]["acceptance"])

    def test_project_delivery_verification_does_not_trigger_repair_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = adapter._synthesize_frontend_test_repair_contracts(
            directive=(
                "FashionGenStudio full delivery. Final npm test and npm run build must pass. "
                "The project includes TypeScript workbenches and src/**/*.test.ts coverage."
            ),
            source_metadata={},
        )

        assert contracts == []

    def test_no_critical_issues_fallback(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": [], "warnings": []}
        msg = adapter._build_pm_retry_message(directive="Fix it", quality=quality, previous_output="old")
        assert "无关键问题信息" in msg

    def test_compacts_oversized_retry_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        quality = {"score": 50, "critical_issues": [], "warnings": []}
        directive = "A" * 8_000 + "Z" * 8_000
        msg = adapter._build_pm_retry_message(directive=directive, quality=quality, previous_output="old")
        assert len(msg) < 8_000
        assert "omitted" in msg


class TestDeterministicContractsFlag:
    def test_accepts_nested_metadata_flag(self) -> None:
        assert PMAdapter._deterministic_pm_contracts_enabled(
            input_data={"metadata": {"deterministic_pm_contracts": True}},
            context={},
        )

    def test_context_flag_still_supported(self) -> None:
        assert PMAdapter._deterministic_pm_contracts_enabled(
            input_data={},
            context={"deterministic_pm_contracts": "yes"},
        )


# ---------------------------------------------------------------------------
# JSON payload extraction
# ---------------------------------------------------------------------------


class TestExtractJsonPayload:
    def test_plain_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"tasks": []}')
        assert result == {"tasks": []}

    def test_fenced_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('```json\n{"tasks": []}\n```')
        assert result == {"tasks": []}

    def test_embedded_json(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('Some text\n{"tasks": []}\nMore text')
        assert result == {"tasks": []}

    def test_python_literal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_json_payload('{"tasks": []}')
        assert result == {"tasks": []}

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("") is None
        assert adapter._extract_json_payload("   ") is None

    def test_invalid_python_literal_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_json_payload("- 建立记账模型: 定义交易实体与校验") is None

    def test_prompt_echo_error_does_not_parse_schema_example_as_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = """
Failed to parse action: 你是 Polaris PM，需要产出可执行任务合同。
需求指令:
请基于 Architect 阶段产物生成 PM 执行任务合同。

## Original Requirement Excerpt
# Product Requirements — 命令行猜数字游戏

## Goal
- 实现命令行猜数字游戏:系统随机生成 1-100 的数字,玩家输入猜测,系统给予高/低提示。

请仅输出 JSON，格式如下：
{
  "tasks": [
    {
      "id": "TASK-1",
      "title": "任务标题",
      "goal": "该任务目标",
      "description": "执行背景与约束",
      "scope": "变更范围摘要",
      "steps": ["步骤1", "步骤2"],
      "acceptance": ["可测验收1", "可测验收2"]
    }
  ]
}
禁止返回 Markdown、解释文本、代码块或工具调用标签。
""".strip()

        assert adapter._extract_task_contracts(response, directive="实现命令行猜数字游戏") == []

    def test_json_dependency_chain_task_is_not_promoted_to_contract(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "实现需求分析与项目骨架搭建",
                        "target_files": ["requirements.md"],
                    },
                    {
                        "id": "TASK-2",
                        "title": "核心计算引擎实现",
                        "target_files": ["calculator.py"],
                    },
                    {
                        "id": "TASK-3",
                        "title": "实现验证、文档与 QA 闭环",
                        "target_files": ["README.md"],
                    },
                    {
                        "id": "TASK-4",
                        "title": "实现(骨架) → TASK-2 (引擎) → TASK-3 (验证+文档)",
                        "target_files": ["src/task-2", "tests"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-1", "TASK-2", "TASK-3"]
        assert all("→" not in item["title"] for item in result)

    def test_json_meta_diagnostic_tasks_are_not_promoted_to_contracts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "事实已补齐",
                        "target_files": ["requirements.md"],
                        "goal": "requirements.md 已读取，需求边界清晰。；满足需求: CLI 科学计算器",
                    },
                    {
                        "id": "TASK-2",
                        "title": "任务数",
                        "scope": ["src", "tests"],
                        "goal": "0 → 需新建 3 个任务形成依赖链。；满足需求: CLI 科学计算器",
                    },
                    {
                        "id": "TASK-3",
                        "title": "实现 CLI 科学计算器入口",
                        "target_files": ["calculator.py"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-3"]
        assert [item["title"] for item in result] == ["实现 CLI 科学计算器入口"]

    def test_json_non_delivery_constraint_tasks_force_recovery(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {"id": "TASK-1", "title": "无现有代码基，需从零构建", "scope": ["src", "tests"]},
                    {
                        "id": "TASK-2",
                        "title": "验收维度强调“基础字符串处理与条件/循环控制流”，实现需体现这些教学/考核点",
                        "scope": ["src", "tests"],
                    },
                    {"id": "TASK-3", "title": "实现必须形成依赖链，避免并行冲突", "scope": ["src", "tests"]},
                    {"id": "TASK-4", "title": "design", "scope": ["src/design", "tests"]},
                    {"id": "TASK-5", "title": "实现calculator", "scope": ["src/calculator", "tests"]},
                    {
                        "id": "TASK-6",
                        "title": "执行至少 5 组测试用例（含正常计算、括号优先级、除零、非法字符、空输入），全部通过",
                        "scope": ["src", "tests"],
                    },
                ]
            },
            ensure_ascii=False,
        )

        assert adapter._extract_task_contracts(response, directive="CLI 科学计算器") == []

    def test_json_test_implementation_with_explicit_target_is_preserved(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        response = json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "执行至少 5 组测试用例并固化回归",
                        "target_files": ["tests/test_calculator.py"],
                    }
                ]
            },
            ensure_ascii=False,
        )

        result = adapter._extract_task_contracts(response, directive="CLI 科学计算器")

        assert [item["id"] for item in result] == ["TASK-1"]
        assert result[0]["target_files"] == ["tests/test_calculator.py"]


# ---------------------------------------------------------------------------
# Task extraction from payload
# ---------------------------------------------------------------------------


class TestExtractTasksFromPayload:
    def test_list_of_dicts(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload([{"title": "A"}, {"title": "B"}])
        assert len(result) == 2

    def test_dict_with_tasks_key(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"tasks": [{"title": "A"}]})
        assert len(result) == 1

    def test_nested_dict(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"data": {"task_list": [{"title": "A"}]}})
        assert len(result) == 1

    def test_mapped_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_tasks_from_payload({"task-1": {"title": "A"}, "t_2": {"title": "B"}})
        assert len(result) == 2

    def test_none_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_tasks_from_payload(None) == []


# ---------------------------------------------------------------------------
# Task extraction from sections
# ---------------------------------------------------------------------------


class TestExtractTasksFromSections:
    def test_heading_and_keys(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "Task 1: Fix bug\ngoal: make it work\nscope: src/\nsteps: a, b\nacceptance: test passes\n"
        result = adapter._extract_tasks_from_sections(text, directive="fix")
        assert len(result) == 1
        # Title gets "实现" prefix because "Fix" is not an action marker
        assert result[0]["title"] == "实现Fix bug"
        assert result[0]["goal"] == "make it work"

    def test_bullet_continuation(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "## Task 2\ntitle: Build feature\nsteps:\n- step one\n- step two\nacceptance:\n- criteria one\n"
        result = adapter._extract_tasks_from_sections(text, directive="build")
        assert len(result) == 1
        assert "step one" in result[0]["steps"]
        # Title gets "实现" prefix because "Build" is an action marker, so no prefix
        assert result[0]["title"] == "Build feature"

    def test_chinese_headings(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "任务 1: 编写修复bug\n目标: 让它工作\n"
        result = adapter._extract_tasks_from_sections(text, directive="fix")
        assert len(result) == 1
        # "编写" is in _ACTION_MARKERS so no prefix is added
        assert result[0]["title"] == "编写修复bug"

    def test_bare_task_heading_is_not_promoted_to_placeholder_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
## Task 1
goal: 实现命令行猜数字游戏核心逻辑
scope: guess_number.py, test_guess_number.py
steps:
- 编写随机数与输入校验逻辑
- 补充单元测试
acceptance:
- pytest -q 通过
- 游戏可在命令行运行
""".strip()

        result = adapter._extract_tasks_from_sections(text, directive="实现命令行猜数字游戏")

        assert len(result) == 1
        assert "命令行猜数字游戏" in result[0]["title"]
        assert result[0]["title"] != "实现Task 1"

    def test_task_preamble_is_not_promoted_to_section_task(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
任务已拆解，风险点已标注，计划如下
- **目标**：确立项目结构
- **范围**：创建 calculator.py 和 README.md
- **验收标准**：python calculator.py 不报错
""".strip()

        result = adapter._extract_tasks_from_sections(text, directive="CLI 科学计算器")

        assert result == []


# ---------------------------------------------------------------------------
# Task extraction from bullets
# ---------------------------------------------------------------------------


class TestExtractTasksFromBullets:
    def test_simple_bullets(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- Fix login\n- Build dashboard\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert len(result) == 2
        # "Fix" is not an action marker, so prefix is added
        assert result[0]["title"] == "实现Fix login"

    def test_numbered_list(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "1. Fix login\n2. Build dashboard\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert len(result) == 2

    def test_with_description(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- Fix login: auth bug\n"
        result = adapter._extract_tasks_from_bullets(text, directive="do")
        assert result[0]["title"] == "实现Fix login"
        assert result[0]["description"] == "auth bug"

    def test_markdown_task_label_uses_meaningful_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = "- **TASK-1: 需求解析与架构设计** — 明确解析策略和交付边界\n"

        result = adapter._extract_tasks_from_bullets(text, directive="实现 CLI 科学计算器")

        assert result[0]["title"] == "需求解析与架构设计"
        assert "TASK-1" not in result[0]["title"]
        assert "**" not in result[0]["title"]

    def test_detail_bullets_are_not_promoted_to_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        text = """
- **目标**：确立项目结构
- **范围**：创建 calculator.py 和 README.md
- **验收标准**：python calculator.py 不报错
- **依赖链**：TASK-1 (requirements) → TASK-2 (implementation) → TASK-3 (verification)
- **全局风险**：解析器边界条件需覆盖
- TASK-1 (骨架) → TASK-2 (引擎) → TASK-3 (验证+QA)
""".strip()

        result = adapter._extract_tasks_from_bullets(text, directive="CLI 科学计算器")

        assert result == []


# ---------------------------------------------------------------------------
# Task contract normalization
# ---------------------------------------------------------------------------


class TestNormalizeTaskContract:
    def test_basic_normalization(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Fix bug", "description": "auth issue"}
        result = adapter._normalize_task_contract(raw, 1, "directive")
        assert result["id"] == "TASK-1"
        # "Fix" is not an action marker, so prefix is added
        assert result["title"] == "实现Fix bug"
        assert result["phase"] == "requirements"
        assert result["assigned_to"] == "Director"

    def test_title_without_action_marker_gets_prefix(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Bug fix"}
        result = adapter._normalize_task_contract(raw, 1, "")
        assert result["title"].startswith("实现")

    def test_infers_scope_from_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "Fix login module"}
        result = adapter._normalize_task_contract(raw, 1, "")
        # After title normalization, title becomes "实现Fix login module"
        # _infer_scope_from_title extracts keywords from the normalized title.
        # "fix" is not a stopword, so first keyword is "fix" -> scope = src/fix
        assert "src/fix" in result["scope"] or "login" in result["scope"].lower()

    def test_projection_metadata_merged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "T",
            "projection_scenario": "s1",
            "project_slug": "lab",
            "projection_requirement": "req",
        }
        result = adapter._normalize_task_contract(raw, 1, "")
        meta = result["metadata"]
        assert meta["projection"]["scenario_id"] == "s1"
        assert meta["projection"]["project_slug"] == "lab"
        assert meta["projection"]["requirement"] == "req"

    def test_execution_backend_in_metadata(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "T", "execution_backend": "projection_generate"}
        result = adapter._normalize_task_contract(raw, 1, "")
        assert result["metadata"]["execution_backend"] == "projection_generate"

    def test_natural_language_scope_is_not_promoted_to_target_files(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现素材管理与本地存储",
            "scope_paths": ["backend 素材 API 路由、数据库模型、文件存储；frontend 素材面板组件、拖拽上传交互"],
        }

        result = adapter._normalize_task_contract(raw, 2, "")

        assert result["scope_paths"] == ["src/", "tests/"]
        assert result["target_files"] == []

    def test_keeps_concrete_relative_scope_paths(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "Implement asset storage",
            "scope_paths": ["src/store", "src/spec/generationSpec.ts", "package.json"],
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["scope_paths"] == ["src/store", "src/spec/generationSpec.ts", "package.json"]

    def test_inline_target_files_are_preferred_over_title_inference(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "README 编写与端到端验证",
            "description": (
                "- **goal**: 交付运行说明文档，执行端到端测试。 "
                '- **scope_paths**: [".", "tests"] '
                '- **target_files**: ["README.md", "tests/test_calculator.py"] '
                "- **steps**: 编写 README 与测试。"
            ),
        }

        result = adapter._normalize_task_contract(raw, 3, "")

        assert result["scope_paths"][:2] == ["README.md", "tests/test_calculator.py"]
        assert result["target_files"] == ["README.md", "tests/test_calculator.py"]

    def test_inline_target_files_accept_backticked_array(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "title": "实现需求解析与工程骨架搭建",
            "description": ('- **target_files**: `["calculator.py", "README.md"]` - **steps**: 创建实现与说明文档。'),
        }

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["target_files"] == ["calculator.py", "README.md"]

    def test_workspace_root_cli_requirement_uses_root_files_not_src_directory_targets(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — CLI 科学计算器

## Goal
- 实现一个命令行交互式计算器,支持 +、-、*、/ 及括号优先级的字符串解析与计算。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根(不是描述,是真实代码文件)。
- 附 README.md 说明如何运行。
""".strip()
        raw = {
            "id": "TASK-1",
            "title": "实现关键约束",
            "goal": "必须落盘真实代码文件到工作区根；创建 calculator.py、parser.py；附带 README.md；满足需求: CLI 科学计算器",
            "target_files": ["src", "tests"],
            "scope_paths": ["src", "tests"],
        }

        result = adapter._normalize_task_contract(raw, 1, directive)

        assert result["target_files"] == ["calculator.py", "parser.py", "README.md"]
        assert "src/" in result["scope_paths"]
        assert "tests/" in result["scope_paths"]
        assert "calculator.py" in result["scope_paths"]
        assert "parser.py" in result["scope_paths"]
        assert "README.md" in result["scope_paths"]

    def test_implementation_task_default_test_wording_does_not_add_test_target(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {
            "id": "TASK-2",
            "title": "核心计算引擎与输入校验实现",
            "target_files": ["calculator.py"],
            "phase": "implementation",
        }

        result = adapter._normalize_task_contract(raw, 2, "CLI 科学计算器")

        assert result["target_files"] == ["calculator.py"]

    def test_verification_task_adds_test_target_for_root_cli_project(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
# Product Requirements — CLI 科学计算器

## Goal
- 实现一个命令行交互式计算器,支持 +、-、*、/ 及括号优先级的字符串解析与计算。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根。
- 附 README.md 说明如何运行。
""".strip()
        raw = {
            "id": "TASK-3",
            "title": "README 完善与端到端验证",
            "target_files": ["README.md"],
            "phase": "verification",
        }

        result = adapter._normalize_task_contract(raw, 3, directive)

        assert result["target_files"] == ["README.md", "tests/test_calculator.py"]
        assert "tests/test_calculator.py" in result["scope_paths"]

    def test_fallback_goal_does_not_echo_prompt_directive(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "**TASK-1"}
        directive = "请基于 Architect 阶段产物生成 PM 执行任务合同。# Product Requirements — CLI 科学计算器\n"

        result = adapter._normalize_task_contract(raw, 1, directive)

        assert result["title"] == "实现CLI 科学计算器"
        assert "请基于" not in result["goal"]
        assert "Architect 阶段产物" not in result["goal"]
        assert "CLI 科学计算器" in result["goal"]

    def test_trailing_task_label_is_removed_from_title(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        raw = {"title": "需求锁定与工程骨架搭建（TASK-1）"}

        result = adapter._normalize_task_contract(raw, 1, "")

        assert result["title"] == "实现需求锁定与工程骨架搭建"


# ---------------------------------------------------------------------------
# List normalization
# ---------------------------------------------------------------------------


class TestNormalizeList:
    def test_string_split(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list("a, b, c") == ["a", "b", "c"]
        assert adapter._normalize_list("a\nb\nc") == ["a", "b", "c"]

    def test_list_passthrough(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list(["a", "b"]) == ["a", "b"]

    def test_none_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_list(None) == []


# ---------------------------------------------------------------------------
# Scope / domain inference
# ---------------------------------------------------------------------------


class TestInferScopeFromTitle:
    def test_extracts_keyword(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Use a title with an action marker so it doesn't get prefixed,
        # and use a keyword that is not in _STOPWORDS.
        result = adapter._infer_scope_from_title("Implement authentication service")
        assert "src/authentication" in result

    def test_fallback_when_no_keywords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._infer_scope_from_title("a")
        assert result == ["src/", "tests/"]


class TestDeriveDomainToken:
    def test_from_workspace_name(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("")
        # workspace name is derived from tmp_path which is random; just assert non-empty string
        assert isinstance(token, str)
        assert token != ""

    def test_from_directive_keywords(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        # Workspace name from tmp_path may take precedence; verify it returns a non-empty string
        token = adapter._derive_domain_token("Keywords: payment-gateway, checkout")
        assert isinstance(token, str) and len(token) >= 3

    def test_from_directive_text(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("Implement the billing module")
        assert isinstance(token, str) and len(token) >= 3

    def test_fallback_project(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        token = adapter._derive_domain_token("a b c")
        # If workspace name yields a token, it will be returned; otherwise "project"
        assert isinstance(token, str) and len(token) >= 3


class TestExtractDomainKeywords:
    def test_limit_respected(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_keywords("one two three four five six", limit=3)
        assert len(result) == 3

    def test_keyword_hint_parsed(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_domain_keywords("Keywords: alpha, beta, gamma")
        assert "alpha" in result

    def test_synthesized_contracts_prefer_original_requirement_subject_over_pm_prompt_noise(
        self, tmp_path: Any
    ) -> None:
        adapter = _make_adapter(tmp_path)
        directive = """
请基于 Architect 阶段产物生成 PM 执行任务合同。任务必须覆盖需求、实现、验证、QA 闭环。

## Original Requirement Excerpt
# Product Requirements — 命令行猜数字游戏

## Goal
- 实现命令行猜数字游戏:系统随机生成 1-100 的数字,玩家输入猜测,系统给予高/低提示,限制 10 次机会,结束显示战绩。

## Acceptance Criteria
- 完整可运行的实现落盘到工作区根。
- 附 README.md 说明如何运行。
""".strip()

        contracts = adapter._synthesize_task_contracts_from_directive(directive=directive)

        titles = [str(item.get("title") or "") for item in contracts]
        assert any("命令行猜数字游戏" in title for title in titles)
        assert all("Task " not in title for title in titles)
        assert all("architect" not in title.lower() for title in titles)


# ---------------------------------------------------------------------------
# Directive complexity analysis
# ---------------------------------------------------------------------------


class TestAnalyzeDirectiveComplexity:
    def test_empty_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._analyze_directive_complexity("", {}) == {}

    def test_low_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._analyze_directive_complexity("fix typo", {})
        assert result["complexity"] == "low"
        assert result["recommended_strategy"] == "minimal_decomposition"

    def test_high_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = (
            "Implement authentication API with database schema, "
            "frontend integration, CI/CD pipeline, and test suite. "
            "If user is admin, show extra panel. Iterate over all records. "
            "Also implement build and define the deployment schema. "
            "Create tests for /src/auth.py, /src/db.py, /src/api.py"
        )
        result = adapter._analyze_directive_complexity(directive, {})
        assert result["complexity"] == "high"
        assert result["recommended_strategy"] == "deep_decomposition"
        assert result["estimated_task_count"] >= 5

    def test_medium_complexity(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        directive = (
            "Implement API with tests and deploy to staging. "
            "If errors occur, retry three times. "
            "Build /src/a.py, /src/b.py, /src/c.py"
        )
        result = adapter._analyze_directive_complexity(directive, {})
        assert result["complexity"] == "medium"


# ---------------------------------------------------------------------------
# Meta-planning hints
# ---------------------------------------------------------------------------


class TestApplyMetaPlanningHints:
    def test_no_analysis_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = "some message"
        assert adapter._apply_meta_planning_hints(msg, {}) == msg

    def test_injects_before_tasks_section(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = 'Header\n"tasks": [\n    {\n      "id": "TASK-1"\n    }\n  ]'
        analysis = {"recommended_strategy": "deep_decomposition", "estimated_task_count": 5}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        assert "Meta-Planning" in result
        assert "deep_decomposition" in result
        assert result.index("Meta-Planning") < result.index('"tasks": [')
        array_prefix = result.split('"tasks": [', 1)[1].split("{", 1)[0]
        assert "Meta-Planning" not in array_prefix

    def test_injects_before_json_format_section(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = 'Header\n请仅输出 JSON，格式如下：\n{\n  "tasks": [\n    {}\n  ]\n}'
        analysis = {"recommended_strategy": "minimal_decomposition", "estimated_task_count": 2}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        assert result.index("Meta-Planning") < result.index("请仅输出 JSON")

    def test_existing_meta_planning_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = '[Meta-Planning] Complexity: high.\n请仅输出 JSON，格式如下：\n{\n  "tasks": []\n}'
        analysis = {"recommended_strategy": "deep_decomposition", "estimated_task_count": 6}
        assert adapter._apply_meta_planning_hints(msg, analysis) == msg

    def test_no_tasks_section_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        msg = "Just some text"
        analysis = {"recommended_strategy": "minimal_decomposition", "estimated_task_count": 2}
        result = adapter._apply_meta_planning_hints(msg, analysis)
        # When no '"tasks": [' section exists, the message is returned unchanged
        assert result == msg


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


class TestNormalizeProjectionProjectSlug:
    def test_basic(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_projection_project_slug("My Project") == "my_project"
        assert adapter._normalize_projection_project_slug("a--b__c") == "a_b_c"

    def test_empty_uses_default(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._normalize_projection_project_slug("") == "projection_lab"


class TestExtractProjectionContractHint:
    def test_non_projection_backend_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._extract_projection_contract_hint(input_data={}, context={}, directive="") == {}

    def test_projection_backend_extracts_fields(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_projection_contract_hint(
            input_data={"execution_backend": "projection_generate", "projection": {"scenario_id": "s1"}},
            context={},
            directive="req",
        )
        assert result["execution_backend"] == "projection_generate"
        assert result["projection"]["scenario_id"] == "s1"
        assert result["projection"]["requirement"] == "req"

    def test_missing_scenario_id_returns_empty(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._extract_projection_contract_hint(
            input_data={"execution_backend": "projection_generate"},
            context={},
            directive="",
        )
        assert result == {}


class TestApplyProjectionContractHint:
    def test_no_hint_returns_unchanged(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        contracts = [{"title": "T"}]
        assert adapter._apply_projection_contract_hint(contracts, projection_hint=None) == contracts

    def test_first_task_gets_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1"}, {"title": "T2"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[0]["metadata"]["projection"]["scenario_id"] == "s1"
        assert result[1]["execution_backend"] == "code_edit"

    def test_preserves_existing_projection_generate(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {
            "execution_backend": "projection_generate",
            "projection": {"scenario_id": "s1"},
        }
        contracts = [{"title": "T1", "execution_backend": "projection_generate"}]
        result = adapter._apply_projection_contract_hint(contracts, projection_hint=hint)
        assert result[0]["execution_backend"] == "projection_generate"


class TestBuildProjectionHintContracts:
    def test_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1", "project_slug": "lab"}}
        result = adapter._build_projection_hint_contracts(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["execution_backend"] == "projection_generate"
        assert result[1]["execution_backend"] == "code_edit"
        assert result[2]["execution_backend"] == "code_edit"


class TestSynthesizeTaskContractsFromDirective:
    def test_without_hint_returns_three_tasks(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        result = adapter._synthesize_task_contracts_from_directive(directive="Implement payment module")
        assert len(result) == 3
        assert all(isinstance(c, dict) for c in result)

    def test_with_hint_uses_projection(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        hint = {"projection": {"scenario_id": "s1"}}
        result = adapter._synthesize_task_contracts_from_directive(directive="req", projection_hint=hint)
        assert len(result) == 3
        assert result[0]["metadata"]["execution_backend"] == "projection_generate"


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------


class TestCanonicalText:
    def test_strips_noise(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._canonical_text("Hello, World!") == "helloworld"
        assert adapter._canonical_text("") == ""


class TestBuildTaskIdentitySignature:
    def test_combines_title_and_goal(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._build_task_identity_signature(title="Fix bug", goal="make it work") == "fixbug::makeitwork"
        assert adapter._build_task_identity_signature(title="", goal="x") == "x"
        assert adapter._build_task_identity_signature(title="x", goal="") == "x"


class TestPickPreferredTaskId:
    def test_prefers_in_progress(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 2, "status": "in_progress"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 2

    def test_prefers_higher_id_on_same_status(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        candidates = [
            {"id": 1, "status": "pending"},
            {"id": 3, "status": "pending"},
        ]
        assert adapter._pick_preferred_task_id(candidates) == 3

    def test_empty_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._pick_preferred_task_id([]) is None


class TestFindExistingTaskMatch:
    def test_signature_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {"fixbug::makeitwork": [{"id": 7, "status": "pending"}]}
        title_index: dict[str, list[dict[str, Any]]] = {}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="make it work", signature_index=sig_index, title_index=title_index
            )
            == 7
        )

    def test_title_match(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 8, "status": "pending"}]}
        assert (
            adapter._find_existing_task_match(
                subject="Fix bug", goal="", signature_index=sig_index, title_index=title_index
            )
            == 8
        )

    def test_fuzzy_match_above_threshold(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        sig_index: dict[str, list[dict[str, Any]]] = {}
        title_index: dict[str, list[dict[str, Any]]] = {"fixbug": [{"id": 9, "status": "pending"}]}
        # "fixbugs" vs "fixbug" ratio is ~0.923, below 0.93 threshold
        assert (
            adapter._find_existing_task_match(
                subject="Fix bugs", goal="", signature_index=sig_index, title_index=title_index
            )
            is None
        )

    def test_no_match_returns_none(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter._find_existing_task_match(subject="X", goal="Y", signature_index={}, title_index={}) is None


# ---------------------------------------------------------------------------
# Adapter identity
# ---------------------------------------------------------------------------


class TestPmAdapterIdentity:
    def test_role_id(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        assert adapter.role_id == "pm"

    def test_capabilities(self, tmp_path: Any) -> None:
        adapter = _make_adapter(tmp_path)
        caps = adapter.get_capabilities()
        assert "analyze_requirements" in caps
        assert "generate_tasks" in caps
