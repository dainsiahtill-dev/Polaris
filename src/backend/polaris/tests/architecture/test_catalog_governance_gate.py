from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

BACKEND_ROOT = Path(__file__).resolve().parents[3]
GATE_SCRIPT = BACKEND_ROOT / "docs" / "governance" / "ci" / "scripts" / "run_catalog_governance_gate.py"
BASELINE_FILE = BACKEND_ROOT / "polaris" / "tests" / "architecture" / "allowlists" / "catalog_governance_gate.baseline.json"
MISMATCH_BASELINE_FILE = (
    BACKEND_ROOT / "polaris" / "tests" / "architecture" / "allowlists" / "manifest_catalog_mismatches.baseline.jsonl"
)
FITNESS_RULES_FILE = BACKEND_ROOT / "docs" / "governance" / "ci" / "fitness-rules.yaml"


def _build_utf8_env() -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("LANG", "en_US.UTF-8")
    env.setdefault("LC_ALL", "en_US.UTF-8")
    return env


def test_catalog_governance_gate_fail_on_new_baseline() -> None:
    assert GATE_SCRIPT.is_file(), f"missing governance gate script: {GATE_SCRIPT}"
    assert BASELINE_FILE.is_file(), f"missing governance baseline: {BASELINE_FILE}"
    assert MISMATCH_BASELINE_FILE.is_file(), f"missing mismatch baseline: {MISMATCH_BASELINE_FILE}"

    command = [
        sys.executable,
        str(GATE_SCRIPT),
        "--workspace",
        str(BACKEND_ROOT),
        "--mode",
        "fail-on-new",
        "--baseline",
        str(BASELINE_FILE),
        "--mismatch-baseline",
        str(MISMATCH_BASELINE_FILE),
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
        "Catalog governance fail-on-new gate failed.\n"
        f"command: {' '.join(command)}\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )

    payload = json.loads(completed.stdout or "{}")
    assert isinstance(payload, dict)
    assert payload.get("mode") == "fail-on-new"
    assert payload.get("new_issue_count") == 0
    # manifest_catalog key is present (even if empty) when --mismatch-baseline is used
    assert "manifest_catalog" in payload
    assert payload["manifest_catalog"].get("new_mismatch_count") == 0


def test_fitness_rules_document_depends_on_gate() -> None:
    assert FITNESS_RULES_FILE.is_file(), f"missing fitness rules file: {FITNESS_RULES_FILE}"

    payload = yaml.safe_load(FITNESS_RULES_FILE.read_text(encoding="utf-8")) or {}
    rules = payload.get("rules")
    assert isinstance(rules, list), "fitness-rules.yaml must define a rules list"

    rule_ids = {str(item.get("id") or "").strip() for item in rules if isinstance(item, dict)}
    assert "declared_cell_dependencies_match_imports" in rule_ids
    assert "manifest_catalog_consistency" in rule_ids
