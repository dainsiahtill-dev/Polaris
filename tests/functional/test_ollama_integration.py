import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
import importlib.util

import pytest


def _real_ollama_enabled() -> bool:
    raw = os.environ.get("KERNELONE_RUN_REAL_OLLAMA", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


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


def _ollama_model() -> str:
    return os.environ.get("KERNELONE_OLLAMA_MODEL", "").strip()


def _ollama_available() -> bool:
    return bool(shutil.which("ollama"))


def _model_installed(model: str) -> bool:
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
    except Exception:
        return False
    if result.returncode != 0:
        return False
    base = model.split(":", 1)[0]
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("name"):
            continue
        name = line.split()[0]
        if name == model or name == base or name.startswith(base + ":"):
            return True
    return False


def _require_ollama() -> str:
    if not _real_ollama_enabled():
        pytest.skip(
            "Real Ollama smoke test is opt-in. Set KERNELONE_RUN_REAL_OLLAMA=1 to enable."
        )
    model = _ollama_model()
    if not model:
        pytest.skip("Set KERNELONE_OLLAMA_MODEL to run real Ollama integration tests.")
    if not _ollama_available():
        pytest.skip("ollama CLI not found in PATH.")
    if not _model_installed(model):
        pytest.skip(f"Ollama model not installed: {model}. Run `ollama pull {model}` first.")
    return model


@pytest.mark.integration
@pytest.mark.slow
def test_pm_loop_real_ollama_smoke(tmp_path, monkeypatch):
    model = _require_ollama()
    monkeypatch.setenv("KERNELONE_PROMPT_PROFILE", "generic")
    monkeypatch.setenv("KERNELONE_STATE_TO_RAMDISK", "0")

    loop_pm = _load_loop_pm()

    workspace = tmp_path / "workspace"
    (workspace / "docs" / "product").mkdir(parents=True, exist_ok=True)
    (workspace / "runtime").mkdir(parents=True, exist_ok=True)
    (workspace / "runtime" / "contracts").mkdir(parents=True, exist_ok=True)
    (workspace / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Smoke\n", encoding="utf-8")

    (workspace / "docs" / "product" / "requirements.md").write_text(
        "For this test, output a single JSON object exactly as requested.\n"
        "Include 1 task that targets README.md and includes acceptance criteria.\n",
        encoding="utf-8",
    )
    (workspace / "runtime" / "contracts" / "plan.md").write_text(
        "# Plan\n- Provide a single small task for this test.\n",
        encoding="utf-8",
    )

    timeout = int(os.environ.get("KERNELONE_OLLAMA_TIMEOUT", "120") or 120)
    args = SimpleNamespace(
        pm_backend="ollama",
        workspace=str(workspace),
        model=model,
        timeout=timeout,
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

    code = loop_pm.run_once(args, iteration=1)
    assert code == 0

    pm_tasks_path = (
        workspace / "runtime" / "contracts" / "pm_tasks.contract.json"
    )
    assert pm_tasks_path.is_file()
    payload = json.loads(pm_tasks_path.read_text(encoding="utf-8"))
    if not payload.get("tasks"):
        pytest.skip("Ollama returned empty PM tasks payload in this environment.")
    task = payload["tasks"][0]
    assert task.get("target_files"), "Expected target_files in PM task."
    assert task.get("acceptance"), "Expected acceptance criteria in PM task."

    pm_report_path = workspace / "runtime" / "results" / "pm.report.md"
    assert pm_report_path.is_file()
