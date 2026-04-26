from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_stress_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "src" / "backend" / "polaris" / "delivery" / "cli" / "stress" / "polaris_stress.py"
    if not module_path.is_file():
        module_path = repo_root / "src" / "backend" / "scripts" / "polaris_stress.py"
    spec = importlib.util.spec_from_file_location("polaris_stress_test", str(module_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["polaris_stress_test"] = module
    spec.loader.exec_module(module)
    return module


def test_build_pm_command_preserves_pm_backend_value() -> None:
    stress = _load_stress_module()
    cmd = stress._build_pm_command(
        Path("C:/Temp/ws"),
        pm_backend="ollama",
        director_iterations=2,
        timeout=1200,
        director_timeout=3600,
        start_from="pm",
        directive_via_stdin=False,
        run_director=True,
    )
    assert "--pm-backend" in cmd
    idx = cmd.index("--pm-backend")
    assert cmd[idx + 1] == "ollama"
    assert "--chief-engineer-mode" in cmd
    ce_idx = cmd.index("--chief-engineer-mode")
    assert cmd[ce_idx + 1] == "on"


def test_build_round_directive_keeps_plain_requirement_text() -> None:
    stress = _load_stress_module()
    plain = "# Product Requirements\n- app/fastapi_entrypoint.py\n- tests/test_main.py"
    rendered = stress._build_round_directive(1, plain)
    assert rendered == plain


def test_build_round_directive_transforms_orchestration_prompt() -> None:
    stress = _load_stress_module()
    prompt = """
    Polaris 多语言循环压力测试系统提示词
    支持的语言类型
    每轮执行步骤
    开始执行压力测试循环
    """
    rendered = stress._build_round_directive(2, prompt)
    lowered = rendered.lower()
    assert "product requirements" in lowered
    assert "acceptance criteria" in lowered
    assert "`" in rendered
    assert "test" in lowered


def test_resolve_phase_timeout_applies_buffer_and_caps() -> None:
    stress = _load_stress_module()
    timeout_value = stress._resolve_phase_timeout(
        base_timeout=120,
        director_timeout=300,
        buffer_seconds=90,
        min_seconds=60,
        max_seconds=500,
    )
    assert timeout_value == 390

    capped = stress._resolve_phase_timeout(
        base_timeout=120,
        director_timeout=900,
        buffer_seconds=200,
        min_seconds=60,
        max_seconds=1000,
    )
    assert capped == 1000


def test_agents_content_usable_requires_instructions_and_utf8() -> None:
    stress = _load_stress_module()
    content = """# AGENTS.md

<INSTRUCTIONS>
- Use UTF-8 explicitly for all text files.
- Follow role pipeline.
- Keep runtime artifacts under .polaris/runtime.
- Include verification commands for each change.
</INSTRUCTIONS>
"""
    assert stress._is_agents_content_usable(content, min_bytes=120)
    assert not stress._is_agents_content_usable("# AGENTS", min_bytes=120)


def test_evaluate_strict_depth_detects_fallback_hits(tmp_path: Path) -> None:
    stress = _load_stress_module()
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    target = workspace / "src" / "monolith_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line_{i}" for i in range(120)), encoding="utf-8")

    log_path = runtime_root / "runs" / "pm-00001" / "engine" / "tasks" / "PM-0001-R1" / "logs" / "director.process.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "[WorkerExecutor] Round 1/1 fallback generated 1 file template(s)\n",
        encoding="utf-8",
    )

    records = [
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
    ]
    pm_contract = {"engine_execution": {"records": records}}
    passed, reasons, primary_file, touches, lines, fallback_hits = stress._evaluate_strict_depth(
        workspace=workspace,
        runtime_root=runtime_root,
        pm_contract=pm_contract,
        directive_text="`src/monolith_service.py`",
        primary_file_hint="",
        min_rounds=3,
        min_primary_lines=80,
        require_llm_output=False,
    )
    assert primary_file == "src/monolith_service.py"
    assert touches == 3
    assert lines >= 80
    assert fallback_hits >= 1
    assert passed is False
    assert any("template_fallback_hits>0" in item for item in reasons)


def test_evaluate_strict_depth_ignores_low_signal_retry_logs(tmp_path: Path) -> None:
    stress = _load_stress_module()
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    target = workspace / "src" / "monolith_service.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line_{i}" for i in range(120)), encoding="utf-8")

    log_path = runtime_root / "runs" / "pm-00001" / "engine" / "tasks" / "PM-0001-R1" / "logs" / "director.process.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "[WorkerExecutor] Round 1/1 low-signal output, retrying with patch-only prompt\n",
        encoding="utf-8",
    )

    records = [
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
        {"result_payload": {"changed_files": ["src/monolith_service.py"]}},
    ]
    pm_contract = {"engine_execution": {"records": records}}
    passed, reasons, _, touches, lines, fallback_hits = stress._evaluate_strict_depth(
        workspace=workspace,
        runtime_root=runtime_root,
        pm_contract=pm_contract,
        directive_text="`src/monolith_service.py`",
        primary_file_hint="",
        min_rounds=3,
        min_primary_lines=80,
        require_llm_output=False,
    )
    assert touches == 3
    assert lines >= 80
    assert fallback_hits == 0
    assert passed is True
    assert not any("template_fallback_hits>0" in item for item in reasons)


def test_pm_fallback_detected_from_notes_and_task_id() -> None:
    stress = _load_stress_module()
    pm_contract = {
        "notes": "Auto-generated fallback tasks because PM returned empty/invalid task list.",
        "tasks": [{"id": "PM-0001-F1", "title": "Requirements fallback bootstrap"}],
    }
    assert stress._pm_fallback_detected(pm_contract) is True


def test_evaluate_strict_depth_fails_when_require_llm_and_pm_fallback(tmp_path: Path) -> None:
    stress = _load_stress_module()
    workspace = tmp_path / "workspace"
    runtime_root = tmp_path / "runtime"
    target = workspace / "src" / "index.js"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"line_{i}" for i in range(60)), encoding="utf-8")
    records = [
        {"result_payload": {"changed_files": ["src/index.js"]}},
        {"result_payload": {"changed_files": ["src/index.js"]}},
    ]
    pm_contract = {
        "notes": "Auto-generated fallback tasks because PM returned empty/invalid task list.",
        "tasks": [{"id": "PM-0001-F1", "title": "Requirements fallback bootstrap"}],
        "engine_execution": {"records": records},
    }
    passed, reasons, _, _, _, _ = stress._evaluate_strict_depth(
        workspace=workspace,
        runtime_root=runtime_root,
        pm_contract=pm_contract,
        directive_text="`src/index.js`",
        primary_file_hint="",
        min_rounds=2,
        min_primary_lines=40,
        require_llm_output=True,
    )
    assert passed is False
    assert "pm_fallback_payload_detected" in reasons
