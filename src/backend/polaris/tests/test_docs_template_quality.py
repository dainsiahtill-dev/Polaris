from __future__ import annotations

from polaris.cells.workspace.integrity.public.service import (
    build_docs_templates,
    default_qa_commands,
)


def test_default_qa_commands_infers_from_hint_text_when_profile_empty() -> None:
    commands = default_qa_commands(
        {},
        hint_text="Build a JavaScript API server with src/index.js and tests/service.test.js",
    )
    assert "npm test" in commands
    assert all("Add project-specific QA commands." not in cmd for cmd in commands)


def test_build_docs_templates_replaces_placeholder_qa_commands() -> None:
    fields = {
        "goal": "Build a JavaScript API server for operations dashboard.",
        "in_scope": "- Implement src/index.js\n- Add tests/service.test.js",
        "out_of_scope": "",
        "constraints": "- Keep UTF-8 encoding",
        "definition_of_done": "- npm test passes",
        "backlog": "- scaffold project\n- implement service",
    }
    docs = build_docs_templates(
        workspace=".",
        mode="minimal",
        fields=fields,
        qa_commands=["Add project-specific QA commands."],
    )
    plan_md = docs.get("docs/product/plan.md", "")
    assert "`npm test`" in plan_md
    assert "Add project-specific QA commands." not in plan_md


def test_build_docs_templates_interface_contract_uses_semantic_capabilities() -> None:
    fields = {
        "goal": "在 C:/Temp/ 孵化大型多人在线贪吃蛇项目，建立可验证交付链路。",
        "in_scope": (
            "- 在 C:/Temp/ 孵化大型多人在线贪吃蛇项目，先输出多文档蓝图并分阶段派发\n"
            "- 拆解核心子系统边界并定义模块间接口契约\n"
            "- 补充验证命令、证据路径与失败回路处理策略\n"
        ),
        "out_of_scope": "",
        "constraints": "- 所有文本文件读写必须显式使用 UTF-8",
        "definition_of_done": "- 关键验证命令执行通过并产生可追溯证据",
        "backlog": "- 拆解模块边界\n- 构建验证闭环",
    }
    docs = build_docs_templates(
        workspace=".",
        mode="minimal",
        fields=fields,
        qa_commands=["python -m pytest -q"],
    )
    contract_md = docs.get("docs/product/interface_contract.md", "")
    assert "验证执行与证据Finalize" in contract_md
    assert "实时事件流接入、排序校验与幂等处理" in contract_md
    assert "状态快照与增量广播的一致性策略" in contract_md
    assert "No Yapping" not in contract_md
