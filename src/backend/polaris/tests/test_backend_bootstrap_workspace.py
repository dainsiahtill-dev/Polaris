from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.bootstrap.backend_bootstrap import BackendBootstrapper
from polaris.domain.models.config_snapshot import ConfigSnapshot


def test_bootstrap_create_application_preserves_workspace_from_config_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "target-workspace"
    workspace.mkdir(parents=True, exist_ok=True)

    snapshot = ConfigSnapshot.merge_sources(
        default={
            "workspace": str(workspace),
            "server.host": "127.0.0.1",
            "server.port": 49977,
            "logging.level": "INFO",
            "llm.model": "test-model",
            "llm.provider": "ollama",
            "pm.backend": "auto",
        }
    )

    bootstrapper = BackendBootstrapper()
    app = asyncio.run(bootstrapper._create_application(snapshot))

    assert Path(str(app.state.settings.workspace)).resolve() == workspace.resolve()


def test_bootstrap_emit_startup_event_writes_machine_readable_stdout(
    capsys,
) -> None:
    bootstrapper = BackendBootstrapper()

    bootstrapper._emit_startup_event(51234, True)

    captured = capsys.readouterr()
    payload = json.loads(captured.out.strip())
    assert payload["event"] == "backend_started"
    assert payload["port"] == 51234
