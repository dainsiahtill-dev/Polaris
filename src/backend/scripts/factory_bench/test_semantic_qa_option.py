"""CLI-to-HTTP semantic QA configuration, without launching role providers."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.public.types import FactoryStartRequest
from scripts.factory_bench._bench_lib import cli
from scripts.factory_bench.test_bench_to_backend_integration import _consumer_batch


@pytest.fixture
def factory_http() -> Iterator[dict[str, Any]]:
    state: dict[str, Any] = {"requests": [], "semantic_required": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            pass

        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
            request = FactoryStartRequest.model_validate(body)
            state["requests"].append((self.path, body))
            state["semantic_required"].append(
                OrchestrationStageExecutor._quality_gate_requires_llm_judgement(
                    {"workspace": request.workspace, "metadata": request.metadata}
                )
            )
            encoded = json.dumps({"run_id": "private-http-run", "status": "pending"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = HTTPServer(("127.0.0.1", 0), Handler)
    state["url"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("require_semantic", [True, False])
def test_cli_semantic_qa_option_reaches_http_and_actual_qa_selector(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, require_semantic: bool
) -> None:
    """Current formal engineering boundary; private synthetic ports, no target execution."""
    options = ("--qa-semantic-review-required",) if require_semantic else ()
    code, summary, traces = _consumer_batch(tmp_path, monkeypatch, arguments=options)
    inputs, http, _ = traces[0]
    request = json.loads((inputs.evidence_dir / "formal-request.json").read_text(encoding="utf-8"))
    host = json.loads(inputs.host_config_file.read_text(encoding="utf-8"))
    assert host["require_qa"] is True
    assert request["role"] == "director" and request["workflow"] == "direct"
    assert request["required_checks"] == []
    assert "require_qa" not in request and "metadata" not in request
    platform = summary["records"][0]["observation"]["platform"]
    assert platform["require_qa"] is True and platform["qa_verdict"] == "not_run"
    assert platform["project_outcome"] == "not_accepted" and code != 0
    assert summary["catalog_quality"] == "UNKNOWN"
    assert [p for m, p, _ in http.calls if m == "POST"] == ["/v2/engineering/runs"]
    assert not inputs.workspace.exists()


def test_semantic_qa_option_rejects_director_resume_before_workspace_creation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    factory_http: dict[str, Any],
) -> None:
    work_dir = tmp_path / "must-not-create"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_factory_bench.py",
            "--start-from",
            "director_resume",
            "--qa-semantic-review-required",
            "--work-dir",
            str(work_dir),
        ],
    )
    monkeypatch.setattr(
        cli,
        "load_projects",
        lambda: pytest.fail("unsupported QA override must reject before catalog or workspace work"),
    )
    assert cli.main() == 2
    assert not work_dir.exists()
    assert factory_http["requests"] == []
