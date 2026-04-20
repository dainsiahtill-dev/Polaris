from __future__ import annotations

import os
import sys

BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for candidate in (BACKEND_ROOT,):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from polaris.delivery.cli.pm.tasks_utils import (
    _extract_requirement_file_candidates,
    build_requirements_fallback_payload,
)


def test_extract_requirement_file_candidates_parses_markdown_paths() -> None:
    requirements = """
    ## 目标文件
    - app/fastapi_entrypoint.py
    - app/stats.py
    - tests/test_stats.py
    """
    files = _extract_requirement_file_candidates(requirements)
    assert "app/fastapi_entrypoint.py" in files
    assert "app/stats.py" in files
    assert "tests/test_stats.py" in files


def test_extract_requirement_file_candidates_ignores_prose_prefixed_paths() -> None:
    requirements = """
    ## 目标文件
    - 见 `docs/product/adr.md`
    - app/fastapi_entrypoint.py
    - 请创建 tests/test_main.py
    - tests/test_main.py
    """
    files = _extract_requirement_file_candidates(requirements)
    assert "app/fastapi_entrypoint.py" in files
    assert "tests/test_main.py" in files
    assert all(not item.startswith("见 ") for item in files)
    assert all("`" not in item for item in files)
    assert all(not item.startswith("docs/") for item in files)


def test_extract_requirement_file_candidates_keeps_package_json_not_package_js() -> None:
    requirements = """
    # JavaScript API
    - package.json
    - src/index.js
    - tests/service.test.js
    """
    files = _extract_requirement_file_candidates(requirements)
    assert "package.json" in files
    assert "package.js" not in files


def test_build_requirements_fallback_payload_generates_tasks() -> None:
    requirements = """
    # Python 项目
    目标文件:
    - app/fastapi_entrypoint.py
    - app/parser.py
    - app/cleaner.py
    - app/stats.py
    - tests/test_stats.py
    """
    payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=1,
        timestamp="2026-02-24 00:00:00",
    )
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) >= 1
    joined_targets = []
    titles = []
    for item in tasks:
        titles.append(str(item.get("title") or "").lower())
        joined_targets.extend(item.get("target_files") or [])
    assert "app/fastapi_entrypoint.py" in joined_targets
    assert any("bootstrap" in title for title in titles)
    assert any("tests" in title for title in titles)
    bootstrap = next(
        (
            item
            for item in tasks
            if "bootstrap" in str(item.get("title") or "").lower()
        ),
        {},
    )
    metadata = bootstrap.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata.get("detected_language") == "python"
    assert isinstance(metadata.get("tech_stack"), dict)
    assert str(bootstrap.get("description") or "").strip()


def test_build_requirements_fallback_payload_extracts_round_tasks() -> None:
    requirements = """
    # Product Requirements
    - src/monolith_service.py
    - tests/test_monolith_service.py
    - tui_runtime.md

    Task 1 (ADD):
    - Create TaskRecord and add_task/get_task/list_tasks in src/monolith_service.py
    - Add baseline tests in tests/test_monolith_service.py

    Task 2 (MODIFY):
    - Update src/monolith_service.py with update_task and complete_task
    - Improve list_tasks sorting and filtering

    Task 3 (DELETE/REFACTOR):
    - Remove deprecated helper from src/monolith_service.py
    - Keep pytest passing in tests/test_monolith_service.py
    """
    payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=3,
        timestamp="2026-02-25 00:00:00",
    )
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) >= 3

    first_three = tasks[:3]
    for task in first_three:
        targets = task.get("target_files") or []
        assert "src/monolith_service.py" in targets

    all_acceptance = "\n".join(
        "\n".join(task.get("acceptance_criteria") or []) for task in first_three
    ).lower()
    assert "add_task" in all_acceptance
    assert "update_task" in all_acceptance
    assert "deprecated helper" in all_acceptance


def test_build_requirements_fallback_payload_creates_impl_and_test_tasks_for_javascript() -> None:
    requirements = """
    # Product Requirements
    Build a JavaScript API server.
    - package.json
    - src/index.js
    - src/service.js
    - src/store.js
    """
    payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=5,
        timestamp="2026-02-27 00:00:00",
    )
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    titles = [str(task.get("title") or "").lower() for task in tasks]
    assert any("bootstrap" in title for title in titles)
    assert any("implementation" in title for title in titles)
    assert any("tests" in title for title in titles)

    joined_targets = []
    for task in tasks:
        joined_targets.extend(task.get("target_files") or [])
    assert "package.js" not in joined_targets
    assert "package.json" in joined_targets
    assert any(str(path).startswith("tests/") for path in joined_targets)


def test_build_requirements_fallback_payload_uses_synthetic_paths_when_no_files_listed() -> None:
    requirements = """
    # Multiplayer Lightning Game
    Build an unattended large-scale multiplayer online lightning game stress-test project.
    Ensure autonomous delivery with stable PM -> ChiefEngineer -> Director -> QA workflow.
    """
    payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=9,
        timestamp="2026-02-27 00:00:00",
    )
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) >= 1
    all_targets = []
    for task in tasks:
        all_targets.extend(task.get("target_files") or [])
    assert any(str(path).startswith("src/") for path in all_targets)
    assert any(str(path).startswith("tests/") for path in all_targets)
    notes = str(payload.get("notes") or "").lower()
    assert "synthetic bootstrap paths" in notes


def test_build_requirements_fallback_payload_honors_docs_stage_without_synthetic_paths() -> None:
    requirements = """
    [PM_DOC_STAGE]
    active_stage_id: DOC-STAGE-02
    active_stage_title: API Contract
    active_document: docs/systems/api_contract.md
    stage_progress: 2/6
    execution_rule: read exactly this document in current PM iteration.

    # Active Stage Document
    Define API contracts and keep tasks scoped to docs/systems/api_contract.md.
    """
    payload = build_requirements_fallback_payload(
        requirements=requirements,
        iteration=10,
        timestamp="2026-02-27 00:00:00",
    )
    assert isinstance(payload, dict)
    tasks = payload.get("tasks")
    assert isinstance(tasks, list)
    assert len(tasks) >= 1

    all_targets: list[str] = []
    for task in tasks:
        all_targets.extend(task.get("target_files") or [])
    assert "docs/systems/api_contract.md" in all_targets
    assert "src/fastapi_entrypoint.py" not in all_targets
    assert "pyproject.toml" not in all_targets

    notes = str(payload.get("notes") or "").lower()
    assert "docs stage strict mode active" in notes

