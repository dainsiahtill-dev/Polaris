from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from polaris.bootstrap.config import Settings
from fastapi.testclient import TestClient
from polaris.cells.runtime.state_owner.public import AppState, Auth
from polaris.delivery.http.app_factory import create_app
from polaris.kernelone.storage import resolve_storage_roots


def _make_client(workspace: Path, *, ramdisk_root: str = "", token: str = "test-runtime-token") -> tuple[TestClient, AppState]:
    settings = Settings(workspace=str(workspace), ramdisk_root=ramdisk_root)
    app = create_app(settings)
    app.state.app_state = AppState(settings=settings)
    app.state.auth = Auth(token=token)
    return TestClient(app, headers={"Authorization": f"Bearer {token}"}), app.state.app_state


def test_runtime_clear_dialogue_scope_clears_runtime_paths(tmp_path: Path) -> None:
    client, _state = _make_client(tmp_path)
    runtime_root = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)

    primary = runtime_root / "events" / "dialogue.transcript.jsonl"
    primary.parent.mkdir(parents=True, exist_ok=True)
    primary.write_text('{"event_id":"a"}\n', encoding="utf-8")

    response = client.post("/runtime/clear", json={"scope": "dialogue"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["scope"] == "dialogue"
    assert payload["cleared_count"] >= 1
    assert primary.read_text(encoding="utf-8") == ""


def test_runtime_storage_layout_exposes_explicit_ramdisk_root(tmp_path: Path) -> None:
    ramdisk_root = tmp_path.parent / f"{tmp_path.name}-runtime-root"
    ramdisk_root.mkdir(parents=True, exist_ok=True)
    client, _state = _make_client(tmp_path, ramdisk_root=str(ramdisk_root))

    response = client.get("/runtime/storage-layout")

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"] == str(tmp_path.resolve())
    assert payload["workspace_abs"] == str(tmp_path.resolve())
    assert payload["ramdisk_root"] == str(ramdisk_root.resolve())
    assert payload["runtime_root"].startswith(str(ramdisk_root.resolve()))


def test_runtime_reset_tasks_clears_runtime_records_and_history(tmp_path: Path) -> None:
    client, state = _make_client(tmp_path)
    runtime_root = Path(resolve_storage_roots(str(tmp_path)).runtime_root)
    runtime_root.mkdir(parents=True, exist_ok=True)

    files_to_create = [
        runtime_root / "events" / "pm.events.jsonl",
        runtime_root / "logs" / "pm.process.log",
        runtime_root / "logs" / "director.process.log",
        runtime_root / "results" / "director.result.json",
        runtime_root / "contracts" / "pm_tasks.contract.json",
        runtime_root / "state" / "pm.state.json",
        runtime_root / "events" / "dialogue.transcript.jsonl",
        runtime_root / "logs" / "director.runlog.md",
        runtime_root / "memory" / "last_state.json",
    ]
    for path in files_to_create:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("seed", encoding="utf-8")

    run_history_file = runtime_root / "runs" / "RUN-001" / "logs" / "pm.process.log"
    run_history_file.parent.mkdir(parents=True, exist_ok=True)
    run_history_file.write_text("seed", encoding="utf-8")

    state.last_pm_payload = {"tasks": [{"id": "PM-1"}]}

    with patch(
        "polaris.delivery.http.routers.runtime.terminate_external_loop_pm_processes",
        return_value=[7788],
    ) as external_pm_mock:
        response = client.post("/runtime/reset-tasks")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["cleared_count"] >= len(files_to_create)
    assert payload["pm_external_terminated_pids"] == [7788]
    assert state.last_pm_payload is None
    external_pm_mock.assert_called_once()
    assert Path(str(external_pm_mock.call_args.args[0])) == tmp_path

    for path in files_to_create:
        assert not path.exists()

    assert not (runtime_root / "runs").exists()


def test_app_shutdown_terminates_managed_and_external_processes(tmp_path: Path) -> None:
    client, state = _make_client(tmp_path)

    class _FakePMService:
        stopped = False

        def get_status(self):
            return {"running": True}

        async def stop(self):
            self.stopped = True

    class _FakeDirectorService:
        stopped = False

        async def get_status(self):
            return {"state": "RUNNING"}

        async def stop(self):
            self.stopped = True

    fake_pm_service = _FakePMService()
    fake_director_service = _FakeDirectorService()

    from polaris.cells.director.execution.public.service import DirectorService
    from polaris.cells.orchestration.pm_planning.public.service import PMService

    class _FakeContainer:
        async def resolve_async(self, cls):
            if cls is PMService:
                return fake_pm_service
            if cls is DirectorService:
                return fake_director_service
            raise AssertionError(f"Unexpected dependency request: {cls!r}")

    async def _fake_get_container():
        return _FakeContainer()

    with (
        patch("polaris.infrastructure.di.container.get_container", _fake_get_container),
        patch(
            "polaris.delivery.http.routers.system.terminate_external_loop_pm_processes",
            return_value=[1024, 2048],
        ) as external_pm_mock,
    ):
        response = client.post("/app/shutdown")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["pm_terminated"] is True
    assert payload["director_terminated"] is True
    assert payload["pm_external_terminated_pids"] == [1024, 2048]
    assert fake_pm_service.stopped is True
    assert fake_director_service.stopped is True
    external_pm_mock.assert_called_once()
    assert Path(str(external_pm_mock.call_args.args[0])) == tmp_path

