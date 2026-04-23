import json
import os

import pytest
from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.public.service import (
    load_llm_test_index,
    reconcile_llm_test_index,
    reset_llm_test_index,
    update_index_with_report,
)
from polaris.cells.storage.layout.internal.settings_utils import get_polaris_root


def _index_path(workspace: str) -> str:
    del workspace
    return os.path.join(get_polaris_root(), ".polaris", "config", "llm", "llm_test_index.json")


@pytest.fixture(autouse=True)
def _isolate_polaris_root(tmp_path, monkeypatch):
    monkeypatch.setenv("POLARIS_ROOT", str(tmp_path))
    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / ".polaris"))


def _make_ramdisk_settings(tmp_path) -> Settings:
    workspace = str(tmp_path)
    ramdisk_root = str(tmp_path.parent / f"{tmp_path.name}_ramdisk")
    os.makedirs(ramdisk_root, exist_ok=True)
    return Settings(workspace=workspace, ramdisk_root=ramdisk_root)


def test_reset_llm_test_index_creates_new_index(tmp_path):
    reset_llm_test_index(str(tmp_path))

    global_index_path = _index_path(str(tmp_path))
    assert os.path.exists(global_index_path)

    index_path = os.path.join(tmp_path, ".polaris", "llm_test_index.json")
    assert os.path.exists(index_path)

    with open(index_path, encoding="utf-8") as f:
        index = json.load(f)

    assert index.get("version") == "2.0"
    assert index.get("roles") == {}
    assert index.get("providers") == {}


def test_load_llm_test_index_returns_empty_when_missing(tmp_path):
    index = load_llm_test_index(str(tmp_path))
    assert index.get("roles") == {}
    assert index.get("providers") == {}


def test_update_index_with_report_updates_roles_and_providers(tmp_path):
    workspace = str(tmp_path)

    report = {
        "test_run_id": "abc123",
        "timestamp": "2024-01-01T00:00:00Z",
        "target": {"role": "pm", "provider_id": "ollama_local", "model": "glm-4.7-flash:latest"},
        "final": {"ready": True, "grade": "PASS"},
        "suites": {"connectivity": {"ok": True}, "response": {"ok": True}},
    }

    update_index_with_report(workspace, report)

    index = load_llm_test_index(workspace)

    assert index["roles"]["pm"]["ready"] is True
    assert index["providers"]["ollama_local"]["model"] == "glm-4.7-flash:latest"

    with open(_index_path(workspace), encoding="utf-8") as f:
        persisted = json.load(f)

    assert persisted["roles"]["pm"]["ready"] is True


def test_reconcile_llm_test_index_scans_reports(tmp_path):
    workspace = str(tmp_path)
    reports_dir = os.path.join(workspace, ".polaris", "runtime", "llm_tests", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    report = {
        "test_run_id": "xyz789",
        "timestamp": "2024-01-01T00:00:00Z",
        "target": {"role": "director", "provider_id": "openai", "model": "gpt-4"},
        "final": {"ready": True, "grade": "PASS"},
        "suites": {},
    }

    with open(os.path.join(reports_dir, "xyz789.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)

    index = reconcile_llm_test_index(workspace)

    assert index["roles"]["director"]["ready"] is True
    assert index["providers"]["openai"]["model"] == "gpt-4"


def test_reconcile_preserves_existing_entries(tmp_path):
    workspace = str(tmp_path)

    # Set up initial index
    initial_index = {
        "roles": {"pm": {"ready": True, "grade": "PASS"}},
        "providers": {"ollama": {"model": "llama3"}},
    }
    index_path = os.path.join(workspace, ".polaris", "llm_test_index.json")
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(initial_index, f)

    # Reconcile with no new reports
    index = reconcile_llm_test_index(workspace)

    assert index["roles"]["pm"]["ready"] is True
    assert index["providers"]["ollama"]["model"] == "llama3"


def test_multiple_reports_same_role_updates_to_latest(tmp_path):
    workspace = str(tmp_path)
    reports_dir = os.path.join(workspace, ".polaris", "runtime", "llm_tests", "reports")
    os.makedirs(reports_dir, exist_ok=True)

    for run_id, timestamp, ready in [("run1", "2024-01-01T00:00:00Z", True), ("run2", "2024-01-02T00:00:00Z", False)]:
        report = {
            "test_run_id": run_id,
            "timestamp": timestamp,
            "target": {"role": "pm", "provider_id": "test", "model": "test-model"},
            "final": {"ready": ready, "grade": "PASS" if ready else "FAIL"},
            "suites": {},
        }
        with open(os.path.join(reports_dir, f"{run_id}.json"), "w", encoding="utf-8") as f:
            json.dump(report, f)

    index = reconcile_llm_test_index(workspace)

    assert index["roles"]["pm"]["last_run_id"] == "run2"


def test_reconcile_preserves_unrelated_providers(tmp_path):
    workspace = str(tmp_path)

    initial_index = {
        "providers": {"existing_provider": {"model": "existing", "ready": True}},
    }
    index_path = os.path.join(workspace, ".polaris", "llm_test_index.json")
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    with open(index_path, "w", encoding="utf-8") as f:
        json.dump(initial_index, f)

    reports_dir = os.path.join(workspace, ".polaris", "runtime", "llm_tests", "reports")
    os.makedirs(reports_dir, exist_ok=True)
    report = {
        "test_run_id": "new",
        "timestamp": "2024-01-01T00:00:00Z",
        "target": {"role": "test", "provider_id": "new_provider", "model": "new"},
        "final": {"ready": True, "grade": "PASS"},
        "suites": {},
    }
    with open(os.path.join(reports_dir, "new.json"), "w", encoding="utf-8") as f:
        json.dump(report, f)

    index = reconcile_llm_test_index(workspace)

    assert index["providers"]["existing_provider"]["model"] == "existing"
    assert index["providers"]["new_provider"]["model"] == "new"


def test_load_llm_test_index_prefers_global_index_over_workspace_copy(tmp_path):
    workspace = str(tmp_path)

    global_index = {
        "roles": {"pm": {"ready": True, "grade": "PASS"}},
        "providers": {"global_provider": {"model": "global-model", "ready": True}},
    }
    workspace_index = {
        "roles": {"pm": {"ready": False, "grade": "FAIL"}},
        "providers": {"workspace_provider": {"model": "workspace-model", "ready": False}},
    }

    global_path = _index_path(workspace)
    os.makedirs(os.path.dirname(global_path), exist_ok=True)
    with open(global_path, "w", encoding="utf-8") as f:
        json.dump(global_index, f)

    workspace_path = os.path.join(workspace, ".polaris", "llm_test_index.json")
    os.makedirs(os.path.dirname(workspace_path), exist_ok=True)
    with open(workspace_path, "w", encoding="utf-8") as f:
        json.dump(workspace_index, f)

    index = load_llm_test_index(workspace)

    assert index["roles"]["pm"]["ready"] is True
    assert "global_provider" in index["providers"]
    assert "workspace_provider" not in index["providers"]


