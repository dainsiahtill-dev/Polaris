from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "src" / "backend"
SCRIPTS_ROOT = BACKEND_ROOT / "scripts"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from domain.verification.write_gate import WriteGate  # noqa: E402
from pm.director_mgmt import run_director_once  # noqa: E402


def _build_args(pm_task_path: str) -> SimpleNamespace:
    return SimpleNamespace(
        director_type="auto",
        director_path="src/backend/scripts/loop-director.py",
        pm_task_path=pm_task_path,
    )


def test_write_gate_blocks_unauthorized_changed_files() -> None:
    result = WriteGate.validate(
        changed_files=["src/app.py", "src/routes/admin.py"],
        act_files=["src/app.py"],
        pm_target_files=["src/app.py"],
    )
    assert result.allowed is False
    assert "act.files scope" in result.reason


def test_write_gate_allows_directory_scope_for_pm_targets() -> None:
    result = WriteGate.validate(
        changed_files=["src/services/upload/handler.py"],
        act_files=["src/services/upload/handler.py"],
        pm_target_files=["src/services"],
    )
    assert result.allowed is True


def test_run_director_once_writes_status_when_task_payload_missing(tmp_path: Path) -> None:
    status_path = tmp_path / "director.status.json"
    args = _build_args(str(tmp_path / "missing.pm_tasks.json"))
    code = run_director_once(
        args=args,
        workspace_full=str(tmp_path),
        iteration=1,
        status_path=str(status_path),
        status_payload={"task_id": "PM-001", "run_id": "pm-00001"},
    )
    assert code == 1
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["running"] is False
    assert payload["exit_code"] == 1
    assert "missing" in str(payload.get("error") or "").lower()


def test_run_director_once_writes_success_status_via_interface(monkeypatch, tmp_path: Path) -> None:
    pm_task_path = tmp_path / "pm_tasks.contract.json"
    pm_task_path.write_text(json.dumps({"tasks": []}, ensure_ascii=False), encoding="utf-8")
    status_path = tmp_path / "director.status.json"
    args = _build_args(str(pm_task_path))

    fake_module = type("FakeInterfaceModule", (), {})()
    fake_module.DIRECTOR_INTERFACE_AVAILABLE = True
    fake_module.run_director_via_interface = lambda **_kwargs: 0
    monkeypatch.setitem(sys.modules, "pm.director_interface_integration", fake_module)

    code = run_director_once(
        args=args,
        workspace_full=str(tmp_path),
        iteration=1,
        status_path=str(status_path),
        status_payload={"task_id": "PM-002", "run_id": "pm-00002"},
    )
    assert code == 0
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["running"] is False
    assert payload["exit_code"] == 0
    assert payload.get("error", "") == ""
