"""Workspace quality Python CLI must resolve ``from src.*`` via workspace PYTHONPATH.

Live L2-12 residual: factory quality ran ``python src/main.py`` as a script.
``sys.path[0]`` became ``src/``, so ``from src.engine import ...`` raised
``ModuleNotFoundError: No module named 'src'``. The same workspace passed with
``PYTHONPATH=<workspace> python src/main.py`` and ``python -m src.main``.
Bench gates already inject workspace PYTHONPATH; the official quality runner
must match. Do not hand-edit generated projects to hide this platform gap.
"""

from __future__ import annotations

import sys
from pathlib import Path

from polaris.cells.factory.pipeline.internal.factory_workspace_quality import (
    WorkspaceQualityRunner,
    workspace_quality_subprocess_env,
)
from polaris.cells.factory.pipeline.internal.factory_workspace_quality_evidence import (
    compact_go_stack_overflow_diagnostic,
    workspace_quality_unclaimed_failing_tu_targets,
)


def test_unittest_discovery_rejects_recursive_test_before_spawn(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_product.py").write_text(
        "import subprocess, sys\n"
        "def test_recursive():\n"
        "    subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests'])\n",
        encoding="utf-8",
    )

    result = WorkspaceQualityRunner(tmp_path).run_command(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"],
        timeout_seconds=2,
    )

    assert result["passed"] is False
    assert result["error"] == "recursive_verifier_invocation_detected"
    assert "tests/test_product.py:3:1" in str(result["diagnostic_excerpt"])


def _write_src_package_cli(workspace: Path, *, engine_value: str) -> None:
    src = workspace / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "engine.py").write_text(f"VALUE = {engine_value!r}\n", encoding="utf-8")
    (src / "main.py").write_text(
        "from src.engine import VALUE\nprint(VALUE)\n",
        encoding="utf-8",
    )


def test_workspace_quality_subprocess_env_pins_workspace_pythonpath(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "/host/shadow")
    monkeypatch.setenv("CI", "0")
    env = workspace_quality_subprocess_env(workspace=tmp_path)
    assert env["PYTHONPATH"] == str(tmp_path.resolve())
    assert env["CI"] == "0"


def test_workspace_quality_python_script_resolves_src_package(tmp_path: Path) -> None:
    _write_src_package_cli(tmp_path, engine_value="workspace-ok")
    result = WorkspaceQualityRunner(tmp_path).run_command(
        [sys.executable, "src/main.py"],
        timeout_seconds=10,
    )
    assert result["passed"] is True
    assert result["exit_code"] == 0
    assert "workspace-ok" in str(result.get("stdout_tail") or "")


def test_workspace_quality_pythonpath_overrides_host_src_shadow(
    tmp_path: Path,
    monkeypatch,
) -> None:
    host = tmp_path / "host"
    workspace = tmp_path / "project"
    _write_src_package_cli(host, engine_value="HOST")
    _write_src_package_cli(workspace, engine_value="WORKSPACE")
    monkeypatch.setenv("PYTHONPATH", str(host.resolve()))

    result = WorkspaceQualityRunner(workspace).run_command(
        [sys.executable, "src/main.py"],
        timeout_seconds=10,
    )
    stdout = str(result.get("stdout_tail") or "")
    assert result["passed"] is True
    assert "WORKSPACE" in stdout
    assert "HOST" not in stdout


def test_python_modulenotfound_leftover_prefers_src_importer(tmp_path: Path) -> None:
    engine = tmp_path / "src" / "engine" / "__init__.py"
    engine.parent.mkdir(parents=True)
    engine.write_text("from waterdrop_rhythm_pad import WaterDropPad\n", encoding="utf-8")
    forecast = tmp_path / "src" / "engine" / "forecast.py"
    forecast.write_text("from waterdrop_rhythm_pad.models import MoodAxis\n", encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_product.py").write_text("from src.engine.forecast import forecast_from_mood\n", encoding="utf-8")
    blob = (
        f'  File "{tmp_path}/tests/test_product.py", line 53, in <module>\n'
        "    from src.engine.forecast import (\n"
        f'  File "{tmp_path}/src/engine/__init__.py", line 25, in <module>\n'
        "    from waterdrop_rhythm_pad import WaterDropPad, build_default_pad\n"
        "ModuleNotFoundError: No module named 'waterdrop_rhythm_pad'\n"
    )
    leftover = workspace_quality_unclaimed_failing_tu_targets(
        [blob],
        claimed_targets=[],
        workspace=tmp_path,
    )
    assert leftover[:2] == ["src/engine/__init__.py", "src/engine/forecast.py"]
    assert "tests/test_product.py" not in leftover[:2]


def test_compact_go_stack_overflow_keeps_repeating_owner_frames() -> None:
    dump = "\n".join(
        [
            "fatal error: stack overflow",
            "runtime.throw()",
            "timecapsulemuseum/engine.(*Service).exhibitionIDs(0xc0)",
            "timecapsulemuseum/engine.(*Service).allCapsules(0xc0)",
        ]
        * 20
    )
    compact = compact_go_stack_overflow_diagnostic(dump)
    assert compact.startswith("fatal error: stack overflow")
    assert "exhibitionIDs" in compact
    assert "allCapsules" in compact
    assert "repeating_frames=" in compact
    assert compact.count("runtime.throw") == 0
    assert len(compact) < 500
