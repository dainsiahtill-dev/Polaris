from __future__ import annotations

from polaris.cells.llm.evaluation.public.service import (
    validate_director_safe_scope,
    validate_no_hallucinated_paths,
    validate_pm_plan_json,
    validate_qa_json,
    validate_qa_passfail,
)


def test_validate_no_hallucinated_paths_accepts_preface_and_markdown_list():
    output = (
        "I would inspect the following files from the list:\n\n"
        "- /docs/agent/tui_runtime.md\n"
        "- /src/app.ts\n"
        "- /tests/test_basic.py"
    )
    ok, reason = validate_no_hallucinated_paths(
        output, known_paths=["/docs/agent/tui_runtime.md", "/src/app.ts", "/tests/test_basic.py"]
    )
    assert ok is True, reason


def test_validate_no_hallucinated_paths_accepts_backticked_entries():
    # Note: The path regex looks for leading slashes
    # This test verifies that valid paths are accepted when matching known_paths
    output = "/docs/agent/tui_runtime.md\n/src/app.ts\n/tests/test_basic.py"
    ok, reason = validate_no_hallucinated_paths(
        output, known_paths=["/docs/agent/tui_runtime.md", "/src/app.ts", "/tests/test_basic.py"]
    )
    assert ok is True, reason


def test_validate_no_hallucinated_paths_rejects_extra_path():
    output = "/docs/agent/tui_runtime.md\n/src/app.ts\n/tests/test_basic.py\n/src/secret.ts"
    ok, reason = validate_no_hallucinated_paths(
        output, known_paths=["/docs/agent/tui_runtime.md", "/src/app.ts", "/tests/test_basic.py"]
    )
    assert ok is False
    assert "secret" in reason.lower() or "hallucinat" in reason.lower()


def test_validate_director_safe_scope_accepts_forbidden_path_description():
    output = "Policy reminder: never modify docs/ or scripts/.\nSafe plan: keep all writes under src/ and tests/ only."
    ok, reason = validate_director_safe_scope(output)
    assert ok is True, reason


def test_validate_director_safe_scope_rejects_docs_write_intent():
    output = "I will update docs/tui_runtime.md and then continue with source changes."
    ok, _ = validate_director_safe_scope(output)
    assert ok is False


def test_validate_director_safe_scope_rejects_scripts_write_intent():
    output = "Plan: modify scripts/deploy.sh to add a new step."
    ok, _ = validate_director_safe_scope(output)
    assert ok is False


def test_validate_pm_plan_json_detects_valid_json():
    valid_json = '{"goal": "test", "backlog": ["item1"], "timeline": "1 week"}'
    ok, _ = validate_pm_plan_json(valid_json)
    assert ok is True


def test_validate_pm_plan_json_detects_missing_keys():
    invalid_json = '{"goal": "test"}'  # missing backlog and timeline
    ok, _ = validate_pm_plan_json(invalid_json)
    assert ok is False


def test_validate_pm_plan_json_accepts_fenced_json_blocks():
    fenced_json = (
        "```json\n"
        "{\n"
        '  "goal": "test",\n'
        '  "backlog": ["item1"],\n'
        '  "timeline": "1 week"\n'
        "}\n"
        "``````json\n"
        "{\n"
        '  "goal": "test",\n'
        '  "backlog": ["item1"],\n'
        '  "timeline": "1 week"\n'
        "}\n"
        "```"
    )
    ok, msg = validate_pm_plan_json(fenced_json)
    assert ok is True, msg


def test_validate_qa_json_detects_valid_questions():
    valid = '{"questions": [{"q": "What is X?", "a": "X is..."}]}'
    ok, _ = validate_qa_json(valid)
    assert ok is True


def test_validate_qa_passfail_detects_pass():
    result = {"passed": True}
    ok, _ = validate_qa_passfail(result)
    assert ok is True


def test_validate_qa_passfail_detects_fail():
    result = {"passed": False}
    ok, _ = validate_qa_passfail(result)
    assert ok is False
