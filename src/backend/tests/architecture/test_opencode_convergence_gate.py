from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[2]
GATE_SCRIPT = (
    BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "run_opencode_convergence_gate.py"
)
FITNESS_RULES_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"
PIPELINE_TEMPLATE_PATH = BACKEND_ROOT / "docs" / "governance" / "ci" / "pipeline.template.yaml"


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def test_opencode_convergence_gate_hard_fail_clean() -> None:
    assert GATE_SCRIPT.is_file(), f"missing gate script: {GATE_SCRIPT}"

    command = [
        sys.executable,
        str(GATE_SCRIPT),
        "--workspace",
        str(BACKEND_ROOT),
        "--mode",
        "hard-fail",
    ]
    completed = subprocess.run(
        command,
        cwd=str(BACKEND_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=_build_utf8_env(),
        timeout=240,
        check=False,
    )

    assert completed.returncode == 0, (
        "opencode convergence gate failed.\n"
        f"command: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout or "{}")
    assert isinstance(payload, dict)
    assert payload.get("mode") == "hard-fail"
    assert payload.get("rule_id") == "opencode_canonical_entrypoint_non_regressive"
    assert payload.get("issue_count") == 0


def test_opencode_convergence_rule_and_stage_declared() -> None:
    rules_payload = yaml.safe_load(FITNESS_RULES_PATH.read_text(encoding="utf-8")) or {}
    pipeline_payload = yaml.safe_load(PIPELINE_TEMPLATE_PATH.read_text(encoding="utf-8")) or {}

    rules = rules_payload.get("rules")
    assert isinstance(rules, list), "fitness-rules.yaml must define a rules list"
    rule_ids = {
        str(item.get("id") or "").strip()
        for item in rules
        if isinstance(item, dict)
    }
    assert "opencode_canonical_entrypoint_non_regressive" in rule_ids

    stages = pipeline_payload.get("stages")
    assert isinstance(stages, list), "pipeline.template.yaml must define stages"
    stage_ids = {
        str(item.get("id") or "").strip()
        for item in stages
        if isinstance(item, dict)
    }
    assert "opencode_convergence_gate" in stage_ids

