from __future__ import annotations

import argparse
import json
from typing import Any

import pytest
from polaris.delivery.cli import router as cli_router
from polaris.delivery.cli.__main__ import create_parser
from polaris.delivery.cli.aggregate_audit import (
    build_aggregate_runtime_audit_package,
    main,
    write_audit_package,
)


@pytest.mark.asyncio
async def test_build_aggregate_runtime_audit_package_plan_only(tmp_path) -> None:
    payload = await build_aggregate_runtime_audit_package(
        workspace=str(tmp_path),
        objective="Audit aggregate runtime.",
    )

    assert payload["status"] == "PASS"
    assert payload["execution_mode"] == "plan_only"
    assert payload["integration_audit"]["status_counts"] == {"wired": 16}
    assert payload["integration_audit"]["missing_entrypoint_count"] == 0
    assert payload["runtime_materialization"]["executed"] is False
    assert payload["full_chain_env_preflight"]["ready"] is False


@pytest.mark.asyncio
async def test_build_aggregate_runtime_audit_package_lobe_chain_materializes(monkeypatch, tmp_path) -> None:
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    captured: list[Any] = []

    async def fake_stream_chat_turn(self, command):
        captured.append((self, command))
        yield {
            "type": "fingerprint",
            "profile_id": f"code.{command.role}",
            "profile_hash": "hash",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": "strategy-run",
            "turn_index": 1,
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": "aggregate audit materialized"}

    monkeypatch.setattr(RoleRuntimeService, "stream_chat_turn", fake_stream_chat_turn)

    payload = await build_aggregate_runtime_audit_package(
        workspace=str(tmp_path),
        objective="Audit aggregate lobe chain.",
        execution_mode="lobe_chain",
        max_lobe_turns=1,
    )

    assert captured
    assert payload["status"] == "PASS"
    assert payload["execution_mode"] == "lobe_chain"
    assert payload["runtime_materialization"]["executed"] is True
    assert payload["runtime_materialization"]["executed_turns"] == 1
    assert payload["runtime_materialization"]["all_unique_technology_ids_materialized"] is True
    assert len(payload["runtime_materialization"]["runtime_integrations_wired"]) == 16


def test_write_audit_package_uses_utf8_json(tmp_path) -> None:
    output = tmp_path / "audit.json"

    written = write_audit_package({"status": "PASS", "message": "聚合LLM"}, output)

    assert written == output
    assert json.loads(output.read_text(encoding="utf-8"))["message"] == "聚合LLM"


def test_aggregate_audit_cli_writes_default_package(tmp_path, capsys) -> None:
    output = tmp_path / "audit.json"

    exit_code = main(["--workspace", str(tmp_path), "--output", str(output)])

    assert exit_code == 0
    body = json.loads(output.read_text(encoding="utf-8"))
    assert body["status"] == "PASS"
    assert body["integration_audit"]["status_counts"] == {"wired": 16}
    assert "output=" in capsys.readouterr().out


def test_unified_cli_parser_accepts_aggregate_audit(tmp_path) -> None:
    parser = create_parser()

    args = parser.parse_args(
        [
            "aggregate-audit",
            "--workspace",
            str(tmp_path),
            "--execution-mode",
            "plan_only",
            "--max-lobe-turns",
            "1",
        ]
    )

    assert args.command == "aggregate-audit"
    assert args.workspace == str(tmp_path)
    assert args.execution_mode == "plan_only"


def test_cli_router_dispatches_aggregate_audit(monkeypatch, tmp_path) -> None:
    captured: dict[str, Any] = {}

    def fake_main(argv: list[str]) -> int:
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(cli_router.WorkspaceGuard, "ensure_workspace", lambda _path: tmp_path)
    monkeypatch.setattr("polaris.delivery.cli.aggregate_audit.main", fake_main)

    args = argparse.Namespace(
        command="aggregate-audit",
        workspace=str(tmp_path),
        objective="Audit aggregate runtime.",
        execution_mode="plan_only",
        max_lobe_turns=1,
        output="",
        pretty=False,
    )

    exit_code = cli_router.CliRouter().route(args)

    assert exit_code == 0
    assert captured["argv"][:2] == ["--workspace", str(tmp_path)]
    assert "--execution-mode" in captured["argv"]
