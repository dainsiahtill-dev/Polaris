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
from scripts.factory_bench import factory_http_client
from scripts.factory_bench._bench_lib import chain, cli


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
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    factory_http: dict[str, Any],
    require_semantic: bool,
) -> None:
    """Dropping the parsed option or either metadata override must fail here."""
    argv = [
        "run_factory_bench.py",
        "--project-ids",
        "L1-01",
        "--work-dir",
        str(tmp_path),
        "--launcher-instance-mode",
        "isolated",
        "--bench-session-reporting",
        "off",
        "--timeout",
        "5400",
        "--start-from",
        "pm",
    ]
    if require_semantic:
        argv.append("--qa-semantic-review-required")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(
        cli,
        "load_projects",
        lambda: [{"id": "L1-01", "level": 1, "title": "Private fixture", "brief": "Build a CLI."}],
    )
    monkeypatch.setattr(cli, "_resolve_backend_url", lambda: factory_http["url"])
    monkeypatch.setattr(cli, "_resolve_backend_token", lambda: "private-fixture-token")
    monkeypatch.setattr(cli, "_ensure_bench_session", lambda **_kwargs: "")
    monkeypatch.setattr(cli, "_emit_bench_event", lambda **_kwargs: None)
    monkeypatch.setattr(cli, "_push_bench_complete_to_backend", lambda **_kwargs: True)
    monkeypatch.setattr(cli, "_bootstrap_audit_workspace", lambda _workspace: {"ok": True})
    monkeypatch.setattr(cli, "_runtime_project_contamination", lambda _workspace: [])
    monkeypatch.setattr(cli, "build_bench_backend_audit_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(cli, "_validate_isolated_bench_launch", lambda **_kwargs: {"ok": True})

    def start_private_instance(**kwargs: Any) -> dict[str, Any]:
        # Replace process launch only; keep real CLI parsing, workspace identity,
        # run_factory_chain payload construction, HTTP encoding and QA selection.
        (Path(kwargs["project_workspace"]) / ".git").mkdir()
        return {"ok": True, "backend_url": factory_http["url"], "token": "private-fixture-token"}

    monkeypatch.setattr(cli, "_start_isolated_bench_project_instance", start_private_instance)

    def send_then_stop(backend_url: str, payload: dict[str, Any], *, token: str) -> None:
        factory_http_client.start_factory_run(backend_url, payload, token=token)
        # Stop before waiting, polling, provider execution or artifact evaluation.
        raise KeyboardInterrupt

    monkeypatch.setattr(chain, "start_factory_run", send_then_stop)
    assert cli.main() == 130
    assert len(factory_http["requests"]) == 1
    path, body = factory_http["requests"][0]
    assert path == "/v2/factory/runs"
    assert body["start_from"] == "pm"
    assert body["persist_workspace"] is False
    assert body["metadata"]["factory_run_timeout_seconds"] == 5400
    assert body["metadata"]["factory_run_deadline_safety_seconds"] == 30
    assert factory_http["semantic_required"] == [require_semantic]
    if require_semantic:
        assert body["metadata"]["qa_semantic_review_required"] is True
        assert body["metadata"]["qa_llm_required"] is True
    else:
        assert "qa_semantic_review_required" not in body["metadata"]
        assert "qa_llm_required" not in body["metadata"]


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
