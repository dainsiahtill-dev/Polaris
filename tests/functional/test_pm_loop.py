import json
import sys
from types import SimpleNamespace
import importlib.util
from pathlib import Path

def _load_loop_pm():
    repo_root = Path(__file__).resolve().parents[2]
    module_dir = repo_root / "src" / "backend" / "core" / "polaris_loop"
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
    module_path = repo_root / "src" / "backend" / "scripts" / "loop-pm.py"
    spec = importlib.util.spec_from_file_location("loop_pm", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load loop-pm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_invoke_ollama(prompt: str, model: str, workspace: str, show_output: bool, timeout: int) -> str:
    payload = {
        "overall_goal": "Functional test",
        "focus": "Create implementation-ready PM tasks for docs bootstrap",
        "tasks": [
            {
                "id": "PM-FUNC-1",
                "priority": 1,
                "phase": "bootstrap",
                "title": "Refactor requirements seed document",
                "goal": "Implement a concrete requirements baseline with explicit scope and measurable acceptance.",
                "description": "Update docs/product/requirements.md to include project objective, constraints, and acceptance gates.",
                "backlog_ref": "REQ-BOOTSTRAP-001",
                "target_files": ["docs/product/requirements.md"],
                "execution_checklist": [
                    "Inspect current `docs/product/requirements.md` content",
                    "Write objective and scope sections",
                    "Record measurable acceptance criteria"
                ],
                "acceptance": [
                    "Run `python -m pytest -q` and verify collection succeeds",
                    "Verify `docs/product/requirements.md` contains objective and scope headings"
                ],
                "metadata": {
                    "doc_sections": ["Objective", "Scope", "Acceptance"]
                },
            },
            {
                "id": "PM-FUNC-2",
                "priority": 2,
                "phase": "verification",
                "title": "Validate PM contract artifacts are written",
                "goal": "Verify PM loop emits runtime contract artifacts and report files.",
                "description": "Check generated contract/report files and assert non-empty JSON payload.",
                "backlog_ref": "REQ-BOOTSTRAP-002",
                "depends_on": ["PM-FUNC-1"],
                "target_files": ["docs/product/requirements.md"],
                "execution_checklist": [
                    "Confirm contract file exists in runtime/contracts",
                    "Load JSON and validate task keys",
                    "Confirm runtime/results/pm.report.md exists"
                ],
                "acceptance": [
                    "Run `python -m pytest tests/functional/test_pm_loop.py -q` and expect exit code 0",
                    "Verify path `runtime/results/pm.report.md` exists"
                ],
                "metadata": {
                    "doc_sections": ["Validation"]
                },
            }
        ],
        "notes": "Generated deterministic tasks for functional pipeline validation.",
    }
    return json.dumps(payload, ensure_ascii=False)


def test_pm_loop_writes_outputs(tmp_path, monkeypatch):
    loop_pm = _load_loop_pm()
    from storage_layout import resolve_storage_roots

    monkeypatch.setenv("POLARIS_STATE_TO_RAMDISK", "0")

    workspace = tmp_path / "workspace"
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / ".polaris" / "runtime").mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")

    (workspace / "docs" / "product").mkdir(parents=True, exist_ok=True)
    (workspace / "docs" / "product" / "requirements.md").write_text("# reqs\n", encoding="utf-8")

    args = SimpleNamespace(
        pm_backend="ollama",
        workspace=str(workspace),
        model="fake",
        timeout=0,
        plan_path="runtime/contracts/plan.md",
        gap_report_path="runtime/contracts/gap_report.md",
        qa_path="runtime/results/qa.review.md",
        requirements_path="docs/product/requirements.md",
        pm_out="runtime/contracts/pm_tasks.contract.json",
        pm_report="runtime/results/pm.report.md",
        state_path="runtime/state/pm.state.json",
        task_history_path="runtime/events/pm.task_history.events.jsonl",
        director_result_path="runtime/results/director.result.json",
        loop=False,
        interval=1,
        max_iterations=0,
        max_failures=5,
        max_blocked=5,
        max_same_task=3,
        stop_on_failure=True,
        heartbeat=False,
        json_log="",
        run_director=False,
        director_path="src/backend/scripts/loop-director.py",
        events_path="runtime/events/runtime.events.jsonl",
        director_model="",
        director_timeout=0,
        director_show_output=False,
        director_result_timeout=100,
        dialogue_path="runtime/events/dialogue.transcript.jsonl",
        prompt_profile="generic",
        pm_last_message_path="runtime/results/pm_last.output.md",
        ramdisk_root="",
        codex_profile="",
        codex_full_auto=True,
        codex_dangerous=False,
    )

    monkeypatch.setattr(loop_pm, "invoke_ollama", _fake_invoke_ollama)
    monkeypatch.setattr(loop_pm, "ensure_ollama_available", lambda: "ollama")
    monkeypatch.setattr(loop_pm, "_invoke_pm_backend", lambda *a, **k: _fake_invoke_ollama("", "", "", False, 0))

    code = loop_pm.run_once(args, iteration=1)
    assert code == 0

    runtime_root = Path(resolve_storage_roots(str(workspace), "").runtime_root)
    pm_tasks_path = runtime_root / "contracts" / "pm_tasks.contract.json"
    assert pm_tasks_path.is_file()
    payload = json.loads(pm_tasks_path.read_text(encoding="utf-8"))
    assert payload["tasks"][0]["id"] == "PM-FUNC-1"

    pm_report_path = runtime_root / "results" / "pm.report.md"
    assert pm_report_path.is_file()
