"""Internal test requirements preserve the brief's actual entrypoint needs."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from scripts.factory_bench._bench_lib.gates import build_requirements_doc


@pytest.mark.parametrize("project_type", ["simulation_toy", "command_line_tool", "api_service"])
def test_requirements_do_not_convert_category_or_language_into_html(project_type: str) -> None:
    project: dict[str, Any] = {
        "id": "GENERIC",
        "title": "Module toolkit",
        "brief": "Implement a runnable toolkit with an executable entrypoint and real rule checks.",
        "level": 1,
        "primary_language": "typescript",
        "project_type": project_type,
        "domain": "generic",
        "checks": ["ts_syntax", "package_scripts"],
    }
    before = deepcopy(project)
    doc = build_requirements_doc(project)
    assert project["brief"] in doc
    assert "Web/visual/simulation/game 项目提供含 <html>" not in doc
    assert "入口方式以原始需求为准" in doc
    assert "Node CLI" in doc
    if "simulation" in project_type:
        assert "真实场景/渲染/交互源码" in doc
    for obsolete_instruction in ("任务合同", "CE 蓝图", "target_files", "PM -> Chief Engineer"):
        assert obsolete_instruction not in doc
    assert project == before


def test_explicit_web_brief_and_html_checks_are_preserved() -> None:
    project: dict[str, Any] = {
        "id": "GENERIC-WEB",
        "title": "Module browser interface",
        "brief": "Deliver a browser interface in public/index.html with interactive controls.",
        "level": 1,
        "primary_language": "typescript",
        "project_type": "web_application",
        "checks": ["html", "ts_syntax"],
    }
    doc = build_requirements_doc(project)
    assert project["brief"] in doc
    assert "- html" in doc
    assert "<html>" in doc
