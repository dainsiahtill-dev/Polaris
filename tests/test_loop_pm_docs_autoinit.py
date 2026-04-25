import importlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


def _load_orchestration_core():
    repo_root = Path(__file__).resolve().parents[1]
    scripts_dir = repo_root / "src" / "backend" / "scripts"
    project_root = repo_root / "src" / "backend"
    loop_module_dir = project_root / "core" / "polaris_loop"
    for entry in (str(scripts_dir), str(project_root), str(loop_module_dir)):
        if entry not in sys.path:
            sys.path.insert(0, entry)
    return importlib.import_module("pm.orchestration_core")


class TestLoopPmDocsAutoInit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_orchestration_core()
        cls.storage_layout = importlib.import_module("storage_layout")

    def test_ensure_docs_ready_auto_initializes_persistent_docs(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as hp_home, patch.dict(
            os.environ,
            {
                "KERNELONE_HOME": hp_home,
                "KERNELONE_DOCS_INIT_MODE": "auto",
            },
            clear=False,
        ):
            self.assertFalse(self.mod.workspace_has_docs(workspace))
            code = self.mod.ensure_docs_ready(workspace)
            self.assertIsNone(code)
            self.assertTrue(self.mod.workspace_has_docs(workspace))
            self.assertFalse((Path(workspace) / "docs").exists())

            docs_root = Path(
                self.storage_layout.resolve_workspace_persistent_path(
                    workspace, "workspace/docs"
                )
            )
            self.assertTrue((docs_root / "agent" / "README.md").is_file())
            self.assertTrue((docs_root / "product" / "requirements.md").is_file())

    def test_ensure_docs_ready_strict_mode_keeps_fail_closed(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as hp_home, patch.dict(
            os.environ,
            {
                "KERNELONE_HOME": hp_home,
                "KERNELONE_DOCS_INIT_MODE": "strict",
            },
            clear=False,
        ):
            self.assertFalse(self.mod.workspace_has_docs(workspace))
            code = self.mod.ensure_docs_ready(workspace)
            self.assertEqual(code, 2)
            self.assertFalse(self.mod.workspace_has_docs(workspace))

    def test_load_state_and_context_ignores_pm_directive_from_file(self):
        with tempfile.TemporaryDirectory() as workspace:
            directive_path = Path(workspace) / "directive.txt"
            directive_path.write_text("Build a tiny static file server", encoding="utf-8")
            args = SimpleNamespace(
                plan_path="runtime/contracts/plan.md",
                gap_report_path="runtime/contracts/gap_report.md",
                qa_path="runtime/results/qa.review.md",
                requirements_path="workspace/docs/product/requirements.md",
                pm_out="runtime/contracts/pm_tasks.contract.json",
                state_path="runtime/state/pm.state.json",
                clear_spin_guard=False,
                directive="",
                directive_file=str(directive_path),
                directive_stdin=False,
                directive_max_chars=200000,
                start_from="pm",
            )
            context = self.mod.load_state_and_context(workspace, "", args, 1)
            self.assertEqual(context["requirements"], "")

    def test_load_state_and_context_architect_directive_builds_in_memory_plan(self):
        with tempfile.TemporaryDirectory() as workspace:
            args = SimpleNamespace(
                plan_path="runtime/contracts/plan.md",
                gap_report_path="runtime/contracts/gap_report.md",
                qa_path="runtime/results/qa.review.md",
                requirements_path="workspace/docs/product/requirements.md",
                pm_out="runtime/contracts/pm_tasks.contract.json",
                state_path="runtime/state/pm.state.json",
                clear_spin_guard=False,
                directive="PulseHUD: topmost transparent HUD with tray and GPU rows",
                directive_file="",
                directive_stdin=False,
                directive_max_chars=200000,
                start_from="architect",
            )
            context = self.mod.load_state_and_context(workspace, "", args, 1)
            self.assertIn("PulseHUD", context["requirements"])
            self.assertIn("Architect Plan (In-Memory)", context["plan_text"])

    def test_load_cli_directive_reads_stdin_in_memory(self):
        args = SimpleNamespace(
            directive="",
            directive_file="",
            directive_stdin=True,
            directive_max_chars=50,
        )
        with patch("sys.stdin", io.StringIO("abc\nxyz\n")):
            value = self.mod._load_cli_directive(args)
        self.assertEqual(value, "abc\nxyz")

    def test_build_backlog_from_directive_filters_heading_noise(self):
        directive = """
        # Polaris 多语言循环压力测试系统提示词
        ## 核心指令
        - 在 X:\\Temp\\polaris_stress\\ 创建测试项目
        - 每轮单项目顺序执行，不并发
        1. 记录详细日志
        """
        backlog = self.mod._build_backlog_from_directive(directive)
        self.assertIn("每轮单项目顺序执行，不并发", backlog)
        self.assertIn("记录详细日志", backlog)
        self.assertNotIn("核心指令", backlog)
        self.assertNotIn("#", backlog)

    def test_extract_project_goal_from_directive_strips_role_meta(self):
        directive = """
        你是 Polaris（自动化无人值守开发工厂）的元架构师
        ## 角色设定
        - Think before you code
        - No Yapping
        - 在 C:/Temp/ 下生成大型多人在线贪吃蛇压力测试项目
        - 输出多文档蓝图并分阶段派发
        """
        goal = self.mod._extract_project_goal_from_directive(directive)
        self.assertIn("C:/Temp/", goal)
        self.assertIn("贪吃蛇", goal)
        self.assertNotIn("Think before you code", goal)
        self.assertNotIn("角色设定", goal)

    def test_extract_project_goal_from_single_line_mixed_directive(self):
        directive = (
            "你是 Polaris 元架构师。角色设定：No Yapping。"
            "核心指令：在 C:/Temp/ 孵化大型多人在线贪吃蛇项目并分阶段派发。"
        )
        goal = self.mod._extract_project_goal_from_directive(directive)
        backlog = self.mod._build_backlog_from_directive(directive)
        self.assertIn("C:/Temp/", goal)
        self.assertIn("贪吃蛇项目", goal)
        self.assertNotIn("No Yapping", goal)
        self.assertNotIn("你是", goal)
        self.assertNotIn("No Yapping", backlog)
        self.assertNotIn("角色设定", backlog)

    def test_extract_project_goal_distills_long_meta_wrapped_directive(self):
        directive = """
        你是 Polaris（自动化无人值守开发工厂）的元架构师
        角色设定：No Yapping
        核心指令：在 C:/Temp/ 孵化大型多人在线贪吃蛇项目，先输出多文档蓝图并分阶段派发，要求每个文档具备可验证验收策略。
        """
        goal = self.mod._extract_project_goal_from_directive(directive)
        self.assertIn("C:/Temp/", goal)
        self.assertIn("贪吃蛇项目", goal)
        self.assertNotIn("No Yapping", goal)
        self.assertNotIn("角色设定", goal)
        self.assertNotIn("先输出多文档蓝图并分阶段派发", goal)

    def test_sanitize_fields_for_templates_removes_prompt_leakage(self):
        payload = {
            "goal": "你是 Polaris 元架构师。在 C:/Temp/ 孵化大型多人在线贪吃蛇项目。",
            "in_scope": "- 角色设定：No Yapping\n- 输出多文档蓝图",
            "out_of_scope": "",
            "constraints": "- 所有文本文件读写必须显式使用 UTF-8",
            "definition_of_done": "- 关键验证命令执行通过并产生可追溯证据",
            "backlog": "No Yapping\n拆解模块边界并定义契约",
        }
        sanitized = self.mod._sanitize_fields_for_templates(payload)
        self.assertIn("C:/Temp/", sanitized["goal"])
        self.assertNotIn("No Yapping", sanitized["goal"])
        self.assertNotIn("No Yapping", sanitized["in_scope"])
        self.assertNotIn("No Yapping", sanitized["backlog"])

    def test_document_quality_gate_rejects_thin_template(self):
        poor = """# Product Requirements

## Goal
TBD
"""
        rich = """# Product Requirements

## Goal
在 C:/Temp/ 构建大型多人在线贪吃蛇压力测试系统，定义端到端交付边界与验证策略。

## Functional Scope
- 提供房间生命周期、玩家匹配、状态广播与断线重连能力。
- 提供服务端权威状态管理与客户端事件订阅模型。
- 提供可回放的运行日志与故障定位证据链。

## Non-Functional Requirements
- 关键路径延迟需可测量并记录。
- 服务异常必须可降级并具备回滚策略。
- 文档、日志、快照统一约束为 UTF-8 编码。

## Acceptance Criteria
- 至少包含构建、验证、回归检查三类可执行命令。
- 每条能力点对应最小证据路径和失败时处理策略。
- 交付内容可被 PM、ChiefEngineer、Director、QA 协同复核。
"""
        self.assertFalse(self.mod._document_quality_ok(poor))
        self.assertTrue(self.mod._document_quality_ok(rich))

    def test_document_quality_gate_rejects_prompt_leakage(self):
        leaked = """# Product Requirements

## Goal
你是 Polaris 元架构师，请输出文档。

## Functional Scope
- A
- B
- C
- D
- E

## Acceptance Criteria
- C1
- C2
- C3
- C4
- C5
"""
        self.assertFalse(self.mod._document_quality_ok(leaked))

    def test_load_state_and_context_doc_stage_advances_when_previous_tasks_terminal(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as hp_home, patch.dict(
            os.environ,
            {
                "KERNELONE_HOME": hp_home,
                "KERNELONE_PM_DOC_STAGE_MODE": "on",
            },
            clear=False,
        ):
            req_rel = "workspace/docs/product/requirements.md"
            contract_rel = "workspace/docs/product/interface_contract.md"
            req_full = self.mod.resolve_artifact_path(workspace, "", req_rel)
            contract_full = self.mod.resolve_artifact_path(workspace, "", contract_rel)
            self.mod.write_text_atomic(req_full, "# req\nREQ-STAGE\n")
            self.mod.write_text_atomic(contract_full, "# contract\nCONTRACT-STAGE\n")

            pipeline_full = self.mod.resolve_artifact_path(
                workspace,
                "",
                "runtime/contracts/architect.docs_pipeline.json",
            )
            self.mod.write_json_atomic(
                pipeline_full,
                {
                    "schema_version": 1,
                    "stages": [
                        {
                            "id": "DOC-STAGE-01",
                            "title": "Requirements",
                            "doc_path": req_rel,
                        },
                        {
                            "id": "DOC-STAGE-02",
                            "title": "Interface Contract",
                            "doc_path": contract_rel,
                        },
                    ],
                },
            )

            args = SimpleNamespace(
                plan_path="runtime/contracts/plan.md",
                gap_report_path="runtime/contracts/gap_report.md",
                qa_path="runtime/results/qa.review.md",
                requirements_path=req_rel,
                pm_out="runtime/contracts/pm_tasks.contract.json",
                state_path="runtime/state/pm.state.json",
                clear_spin_guard=False,
                directive="",
                directive_file="",
                directive_stdin=False,
                directive_max_chars=200000,
                start_from="pm",
            )

            context1 = self.mod.load_state_and_context(workspace, "", args, 1)
            self.assertTrue(context1["docs_stage"]["enabled"])
            self.assertEqual(context1["docs_stage"]["active_doc_path"], req_rel)
            self.assertIn("REQ-STAGE", context1["requirements"])

            pm_out_full = self.mod.resolve_artifact_path(
                workspace,
                "",
                "runtime/contracts/pm_tasks.contract.json",
            )
            with open(pm_out_full, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "tasks": [
                            {"id": "PM-1", "status": "done"},
                            {"id": "PM-2", "status": "failed"},
                        ]
                    },
                    handle,
                    ensure_ascii=False,
                )

            context2 = self.mod.load_state_and_context(workspace, "", args, 2)
            self.assertEqual(context2["docs_stage"]["active_doc_path"], contract_rel)
            self.assertIn("CONTRACT-STAGE", context2["requirements"])

    def test_load_state_and_context_doc_stage_waits_when_previous_tasks_not_terminal(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as hp_home, patch.dict(
            os.environ,
            {
                "KERNELONE_HOME": hp_home,
                "KERNELONE_PM_DOC_STAGE_MODE": "on",
            },
            clear=False,
        ):
            req_rel = "workspace/docs/product/requirements.md"
            contract_rel = "workspace/docs/product/interface_contract.md"
            self.mod.write_text_atomic(
                self.mod.resolve_artifact_path(workspace, "", req_rel),
                "# req\nREQ-STAGE\n",
            )
            self.mod.write_text_atomic(
                self.mod.resolve_artifact_path(workspace, "", contract_rel),
                "# contract\nCONTRACT-STAGE\n",
            )
            self.mod.write_json_atomic(
                self.mod.resolve_artifact_path(
                    workspace,
                    "",
                    "runtime/contracts/architect.docs_pipeline.json",
                ),
                {
                    "schema_version": 1,
                    "stages": [
                        {"id": "DOC-STAGE-01", "title": "Requirements", "doc_path": req_rel},
                        {"id": "DOC-STAGE-02", "title": "Interface Contract", "doc_path": contract_rel},
                    ],
                },
            )

            args = SimpleNamespace(
                plan_path="runtime/contracts/plan.md",
                gap_report_path="runtime/contracts/gap_report.md",
                qa_path="runtime/results/qa.review.md",
                requirements_path=req_rel,
                pm_out="runtime/contracts/pm_tasks.contract.json",
                state_path="runtime/state/pm.state.json",
                clear_spin_guard=False,
                directive="",
                directive_file="",
                directive_stdin=False,
                directive_max_chars=200000,
                start_from="pm",
            )
            _ = self.mod.load_state_and_context(workspace, "", args, 1)

            pm_out_full = self.mod.resolve_artifact_path(
                workspace,
                "",
                "runtime/contracts/pm_tasks.contract.json",
            )
            with open(pm_out_full, "w", encoding="utf-8") as handle:
                json.dump(
                    {"tasks": [{"id": "PM-1", "status": "todo"}]},
                    handle,
                    ensure_ascii=False,
                )

            context2 = self.mod.load_state_and_context(workspace, "", args, 2)
            self.assertEqual(context2["docs_stage"]["active_doc_path"], req_rel)

    def test_write_architect_docs_pipeline_materializes_blueprints_manifest(self):
        with tempfile.TemporaryDirectory() as workspace, tempfile.TemporaryDirectory() as hp_home, patch.dict(
            os.environ,
            {
                "KERNELONE_HOME": hp_home,
            },
            clear=False,
        ):
            req_rel = "workspace/docs/product/requirements.md"
            adr_rel = "workspace/docs/product/adr.md"
            req_full = self.mod.resolve_artifact_path(workspace, "", req_rel)
            adr_full = self.mod.resolve_artifact_path(workspace, "", adr_rel)
            self.mod.write_text_atomic(req_full, "# req\nstage-1\n")
            self.mod.write_text_atomic(adr_full, "# adr\nstage-2\n")

            result = self.mod._write_architect_docs_pipeline(
                workspace,
                "",
                [req_rel, adr_rel],
            )
            self.assertEqual(int(result.get("blueprint_doc_count") or 0), 2)

            manifest_full = self.mod.resolve_artifact_path(
                workspace,
                "",
                "workspace/blueprints/manifest.json",
            )
            with open(manifest_full, "r", encoding="utf-8") as handle:
                manifest = json.load(handle)

            docs = manifest.get("docs")
            self.assertIsInstance(docs, list)
            self.assertEqual(len(docs), 2)
            first = docs[0]
            self.assertEqual(first.get("phase"), 1)
            self.assertTrue(str(first.get("doc_id") or "").startswith("DOC-STAGE-"))

            first_doc_rel = str(first.get("doc_path") or "").strip()
            first_doc_full = self.mod.resolve_artifact_path(
                workspace,
                "",
                first_doc_rel,
            )
            self.assertTrue(Path(first_doc_full).is_file())


if __name__ == "__main__":
    raise SystemExit(unittest.main())
