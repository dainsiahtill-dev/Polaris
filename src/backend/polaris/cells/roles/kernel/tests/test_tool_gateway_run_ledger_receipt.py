"""Tests for RoleToolGateway Run Ledger tool receipt emission."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from polaris.cells.control_plane.run_ledger.public.ledger import RunLedger
from polaris.cells.roles.kernel.internal.kernel.tool_executor import (
    derive_role_turn_capability_scope,
    derive_role_turn_capability_token,
)
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.profile.internal.schema import RoleTurnRequest


def _make_profile(tool_policy: dict[str, Any] | None = None) -> MagicMock:
    profile = MagicMock()
    profile.role_id = "director"
    profile.tool_policy = MagicMock()
    profile.tool_policy.policy_id = "test-policy"
    profile.tool_policy.whitelist = None
    profile.tool_policy.blacklist = []
    profile.tool_policy.allow_code_write = True
    profile.tool_policy.allow_command_execution = True
    profile.tool_policy.allow_file_delete = True
    profile.tool_policy.max_tool_calls_per_turn = 100
    return profile


class TestIsMutationTool:
    """Verify _is_mutation_tool classification."""

    def test_write_file_is_mutation(self) -> None:
        gateway = RoleToolGateway(_make_profile(), workspace="/tmp")
        assert gateway._is_mutation_tool("write_file") is True

    def test_execute_command_is_mutation(self) -> None:
        gateway = RoleToolGateway(_make_profile(), workspace="/tmp")
        assert gateway._is_mutation_tool("execute_command") is True

    def test_search_code_is_not_mutation(self) -> None:
        gateway = RoleToolGateway(_make_profile(), workspace="/tmp")
        assert gateway._is_mutation_tool("search_code") is False

    def test_read_file_is_not_mutation(self) -> None:
        gateway = RoleToolGateway(_make_profile(), workspace="/tmp")
        assert gateway._is_mutation_tool("read_file") is False


class TestAppendToolReceiptToRunLedger:
    """Verify Run Ledger receipt emission on mutation tool success."""

    def test_appends_receipt_for_successful_write(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        gateway = RoleToolGateway(
            _make_profile(),
            workspace=workspace,
            run_id="test-run-001",
            task_id="TASK-1",
            capability_token={"token_id": "jt-abc123", "execution_envelope_hash": "env-hash-123"},
        )
        gateway._append_tool_receipt_to_run_ledger(
            tool_name="write_file",
            execution_args={"path": "src/index.ts", "content": "..."},
            effect_receipt={"old_hash": "aaa", "new_hash": "bbb"},
            normalized_success=True,
        )
        events = RunLedger(Path(workspace), run_id="test-run-001").read_events()
        assert len(events) == 1
        event = events[0]
        assert event["event_type"] == "tool_receipt"
        assert event["tool"] == "write_file"
        assert event["target_path"] == "src/index.ts"
        assert event["job_token_id"] == "jt-abc123"
        assert event["execution_envelope_hash"] == "env-hash-123"
        assert event["task_id"] == "TASK-1"
        assert event["file_hash_delta"]["old"] == "aaa"
        assert event["file_hash_delta"]["new"] == "bbb"
        assert event["file_hash_delta"]["changed"] is True
        assert "content_id" in event
        assert "event_id" in event

    def test_skips_on_failure(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        gateway = RoleToolGateway(
            _make_profile(),
            workspace=workspace,
            run_id="test-run-002",
        )
        gateway._append_tool_receipt_to_run_ledger(
            tool_name="write_file",
            execution_args={"path": "src/index.ts"},
            effect_receipt=None,
            normalized_success=False,
        )
        events = RunLedger(Path(workspace), run_id="test-run-002").read_events()
        assert len(events) == 0

    def test_skips_for_read_only_tool(self, tmp_path: Path) -> None:
        workspace = str(tmp_path)
        gateway = RoleToolGateway(
            _make_profile(),
            workspace=workspace,
            run_id="test-run-003",
        )
        gateway._append_tool_receipt_to_run_ledger(
            tool_name="read_file",
            execution_args={"path": "src/index.ts"},
            effect_receipt=None,
            normalized_success=True,
        )
        events = RunLedger(Path(workspace), run_id="test-run-003").read_events()
        assert len(events) == 0

    def test_skips_when_no_run_id(self, tmp_path: Path) -> None:
        gateway = RoleToolGateway(
            _make_profile(),
            workspace=str(tmp_path),
            run_id="",
        )
        gateway._append_tool_receipt_to_run_ledger(
            tool_name="write_file",
            execution_args={"path": "src/index.ts"},
            effect_receipt=None,
            normalized_success=True,
        )
        # No run_id → no ledger file → no events

    def test_survives_os_error_gracefully(self, tmp_path: Path) -> None:
        """Verify that ledger append errors are swallowed, not raised."""
        gateway = RoleToolGateway(
            _make_profile(),
            workspace="/nonexistent/path/that/does/not/exist",
            run_id="test-run-005",
        )
        # Should not raise
        gateway._append_tool_receipt_to_run_ledger(
            tool_name="write_file",
            execution_args={"path": "src/index.ts"},
            effect_receipt=None,
            normalized_success=True,
        )


class TestRoleTurnCapabilityToken:
    """Verify role-turn Job Token evidence carries envelope command scope."""

    def test_derives_allowed_commands_from_job_token(self) -> None:
        request = RoleTurnRequest(
            message="run verification",
            metadata={
                "execution_envelope_hash": "env-hash",
                "job_token": {
                    "token_id": "job-1",
                    "allowed_paths": ["src/main.py"],
                    "allowed_commands": ["python --version"],
                    "execution_envelope_hash": "env-hash",
                },
            },
        )

        capability_scope = derive_role_turn_capability_scope(request)
        capability_token = derive_role_turn_capability_token(request, capability_scope)

        assert capability_scope == ("src/main.py",)
        assert capability_token["token_id"] == "job-1"
        assert capability_token["execution_envelope_hash"] == "env-hash"
        assert capability_token["allowed_commands"] == ["python --version"]

    def test_derives_capability_from_execution_envelope_authorization(self) -> None:
        request = RoleTurnRequest(
            message="run verification",
            metadata={
                "director_execution_envelope": {
                    "schema_version": "polaris.execution_envelope.v1",
                    "envelope_id": "exec-env-1",
                    "run_id": "run-1",
                    "workspace": "/workspace",
                    "envelope_hash": "env-hash",
                    "pm_contract": {"hash": "pm-hash"},
                    "ce_blueprint": {"hash": "blueprint-hash"},
                    "handoff_decision": {"allowed": True},
                    "authorization": {
                        "capability_token_ref": "job-env",
                        "allowed_write_paths": ["src/main.py"],
                        "target_files": ["src/main.py"],
                        "allowed_commands": ["python --version"],
                    },
                },
            },
        )

        capability_scope = derive_role_turn_capability_scope(request)
        capability_token = derive_role_turn_capability_token(request, capability_scope)

        assert capability_scope == ("src/main.py",)
        assert capability_token["source"] == "director.execution_envelope.authorization"
        assert capability_token["token_id"] == "job-env"
        assert capability_token["contract_hash"] == "pm-hash"
        assert capability_token["blueprint_hash"] == "blueprint-hash"
        assert capability_token["execution_envelope_hash"] == "env-hash"
        assert capability_token["allowed_commands"] == ["python --version"]
