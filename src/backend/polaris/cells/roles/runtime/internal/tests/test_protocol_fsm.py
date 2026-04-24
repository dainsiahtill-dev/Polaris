"""Tests for protocol_fsm module."""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path

import pytest

from polaris.cells.roles.runtime.internal.protocol_fsm import (
    ProtocolFSM,
    ProtocolRequest,
    ProtocolType,
    ProtocolBus,
    RequestStatus,
    create_protocol_fsm,
    create_protocol_bus,
)


class TestProtocolFSM:
    """Tests for ProtocolFSM core logic."""

    def test_create_request_returns_request_id(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            protocol_type=ProtocolType.PLAN_APPROVAL,
            from_role="PM",
            to_role="QA",
            content={"plan": "deploy v2"},
        )
        assert isinstance(request_id, str)
        assert len(request_id) == 8

    def test_get_status_pending(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            ProtocolType.SHUTDOWN, "PM", "Director", {"reason": "done"}
        )
        status = fsm.get_status(request_id)
        assert status == RequestStatus.PENDING

    def test_get_status_nonexistent(self):
        fsm = ProtocolFSM()
        status = fsm.get_status("nonexistent")
        assert status is None

    def test_approve_success(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            ProtocolType.PLAN_APPROVAL, "PM", "QA", {"plan": "step1"}
        )
        result = fsm.approve(request_id, approver="QA", notes="looks good")
        assert result is True
        assert fsm.get_status(request_id) == RequestStatus.APPROVED

    def test_approve_already_approved(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            ProtocolType.PLAN_APPROVAL, "PM", "QA", {"plan": "step1"}
        )
        fsm.approve(request_id, approver="QA")
        result = fsm.approve(request_id, approver="QA2")
        assert result is False

    def test_approve_nonexistent(self):
        fsm = ProtocolFSM()
        result = fsm.approve("nonexistent", approver="QA")
        assert result is False

    def test_reject_success(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            ProtocolType.BUDGET_CHECK, "Director", "CFO", {"amount": 1000}
        )
        result = fsm.reject(request_id, rejecter="CFO", reason="over budget")
        assert result is True
        assert fsm.get_status(request_id) == RequestStatus.REJECTED

    def test_reject_nonexistent(self):
        fsm = ProtocolFSM()
        result = fsm.reject("nonexistent", rejecter="CFO", reason="bad")
        assert result is False

    def test_get_request_returns_request(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(
            ProtocolType.TAKEOVER, "Director", "HR", {"reason": "escalate"}
        )
        request = fsm.get_request(request_id)
        assert request is not None
        assert request.request_id == request_id
        assert request.protocol_type == ProtocolType.TAKEOVER
        assert request.from_role == "Director"

    def test_get_request_nonexistent(self):
        fsm = ProtocolFSM()
        request = fsm.get_request("nonexistent")
        assert request is None

    def test_list_pending_filters_by_type(self):
        fsm = ProtocolFSM()
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "QA", {})
        fsm.create_request(ProtocolType.SHUTDOWN, "Director", "PM", {})
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "Architect", {})

        pending_plan = fsm.list_pending(protocol_type=ProtocolType.PLAN_APPROVAL)
        assert len(pending_plan) == 2
        assert all(r.protocol_type == ProtocolType.PLAN_APPROVAL for r in pending_plan)

    def test_list_pending_filters_by_to_role(self):
        fsm = ProtocolFSM()
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "QA", {})
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "Architect", {})

        pending_qa = fsm.list_pending(to_role="QA")
        assert len(pending_qa) == 1
        assert pending_qa[0].to_role == "QA"

    def test_list_pending_combined_filters(self):
        fsm = ProtocolFSM()
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "QA", {})
        fsm.create_request(ProtocolType.SHUTDOWN, "Director", "PM", {})
        fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "Architect", {})

        pending = fsm.list_pending(
            protocol_type=ProtocolType.PLAN_APPROVAL, to_role="Architect"
        )
        assert len(pending) == 1
        assert pending[0].to_role == "Architect"

    def test_approve_updates_metadata(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(ProtocolType.POLICY_CHECK, "QA", "Auditor", {})
        fsm.approve(request_id, approver="Auditor", notes="compliant")

        request = fsm.get_request(request_id)
        assert request.metadata["approver"] == "Auditor"
        assert request.metadata["notes"] == "compliant"

    def test_reject_updates_metadata(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(ProtocolType.BUDGET_CHECK, "CFO", "PM", {})
        fsm.reject(request_id, rejecter="PM", reason="exceeds limit")

        request = fsm.get_request(request_id)
        assert request.metadata["rejecter"] == "PM"
        assert request.metadata["reason"] == "exceeds limit"

    def test_concurrent_approve_reject(self):
        """Test thread-safe concurrent access to FSM."""
        fsm = ProtocolFSM()
        request_id = fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "QA", {})
        results: dict[str, bool] = {}

        def approve_thread():
            results["approve"] = fsm.approve(request_id, approver="QA1")

        def reject_thread():
            results["reject"] = fsm.reject(request_id, rejecter="QA2", reason="conflict")

        t1 = threading.Thread(target=approve_thread)
        t2 = threading.Thread(target=reject_thread)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        # One must succeed, one must fail (only one state transition)
        assert results["approve"] != results["reject"]

    def test_request_timestamps_updated_on_state_change(self):
        fsm = ProtocolFSM()
        request_id = fsm.create_request(ProtocolType.SHUTDOWN, "PM", "Director", {})
        original_request = fsm.get_request(request_id)
        original_updated = original_request.updated_at

        time.sleep(0.01)  # Ensure time difference
        fsm.approve(request_id, approver="Director")

        updated_request = fsm.get_request(request_id)
        assert updated_request.updated_at >= original_updated


class TestProtocolFSMPersistence:
    """Tests for ProtocolFSM disk persistence."""

    def test_persist_request_writes_json(self, tmp_path):
        fsm = ProtocolFSM(workspace=str(tmp_path))
        request_id = fsm.create_request(
            ProtocolType.PLAN_APPROVAL, "PM", "QA", {"plan": "deploy"}
        )

        protocol_dir = tmp_path / ".polaris" / "runtime" / "state" / "protocols"
        file_path = protocol_dir / f"{request_id}.json"
        assert file_path.exists()

        data = json.loads(file_path.read_text(encoding="utf-8"))
        assert data["request_id"] == request_id
        assert data["protocol_type"] == "plan_approval"
        assert data["from_role"] == "PM"

    def test_load_persistent_restores_requests(self, tmp_path):
        # Pre-create a request JSON file
        protocol_dir = tmp_path / ".polaris" / "runtime" / "state" / "protocols"
        protocol_dir.mkdir(parents=True, exist_ok=True)

        request_data = {
            "request_id": "test1234",
            "protocol_type": "shutdown",
            "from_role": "Director",
            "to_role": "PM",
            "content": {"reason": "done"},
            "status": "approved",
            "created_at": 1234567890.0,
            "updated_at": 1234567891.0,
            "metadata": {"approver": "PM", "notes": "ok"},
        }
        (protocol_dir / "test1234.json").write_text(
            json.dumps(request_data, ensure_ascii=False), encoding="utf-8"
        )

        fsm = ProtocolFSM(workspace=str(tmp_path))
        fsm.load_persistent()

        request = fsm.get_request("test1234")
        assert request is not None
        assert request.protocol_type == ProtocolType.SHUTDOWN
        assert request.from_role == "Director"
        assert request.status == RequestStatus.APPROVED

    def test_load_persistent_ignores_invalid_json(self, tmp_path):
        protocol_dir = tmp_path / ".polaris" / "runtime" / "state" / "protocols"
        protocol_dir.mkdir(parents=True, exist_ok=True)
        (protocol_dir / "bad.json").write_text("not valid json", encoding="utf-8")

        fsm = ProtocolFSM(workspace=str(tmp_path))
        fsm.load_persistent()  # Should not raise

    def test_cleanup_completed_requests(self, tmp_path):
        fsm = ProtocolFSM(workspace=str(tmp_path), max_completed_requests=2)

        # Create 3 requests and approve 3
        ids = []
        for i in range(3):
            rid = fsm.create_request(ProtocolType.PLAN_APPROVAL, "PM", "QA", {"n": i})
            ids.append(rid)
            fsm.approve(rid, approver="QA")

        # With max_completed=2, only 2 should remain
        assert len(fsm._requests) <= 2


class TestProtocolBus:
    """Tests for ProtocolBus message bus."""

    def test_send_returns_message(self, tmp_path):
        bus = ProtocolBus(workspace=str(tmp_path))
        result = bus.send(
            from_role="PM",
            to_role="Director",
            content="execute plan",
            msg_type="task",
        )
        assert "Sent" in result
        assert "task" in result
        assert "Director" in result

    def test_broadcast_returns_count(self, tmp_path):
        bus = ProtocolBus(workspace=str(tmp_path))
        result = bus.broadcast(from_role="PM", content="meeting start")
        assert "Broadcast" in result
        assert "7 roles" in result  # 8 total roles minus sender

    def test_read_inbox_empty(self, tmp_path):
        bus = ProtocolBus(workspace=str(tmp_path))
        messages = bus.read_inbox("PM")
        assert messages == []

    def test_read_inbox_drain(self, tmp_path):
        bus = ProtocolBus(workspace=str(tmp_path))
        bus.send(from_role="Director", to_role="PM", content="task 1")
        bus.send(from_role="Director", to_role="PM", content="task 2")

        messages = bus.read_inbox("PM", drain=True)
        assert len(messages) == 2
        assert messages[0]["content"] == "task 1"
        assert messages[1]["content"] == "task 2"

        # After drain, inbox should be empty
        messages2 = bus.read_inbox("PM", drain=True)
        assert messages2 == []

    def test_read_inbox_no_drain(self, tmp_path):
        bus = ProtocolBus(workspace=str(tmp_path))
        bus.send(from_role="Director", to_role="PM", content="task 1")

        messages = bus.read_inbox("PM", drain=False)
        assert len(messages) == 1

        # Inbox still has message
        messages2 = bus.read_inbox("PM", drain=False)
        assert len(messages2) == 1

    def test_no_workspace_no_io(self):
        bus = ProtocolBus(workspace=None)
        result = bus.send(from_role="PM", to_role="Director", content="test")
        assert result == "Sent task to Director"


class TestProtocolEnums:
    """Tests for ProtocolType and RequestStatus enums."""

    def test_protocol_type_values(self):
        assert ProtocolType.PLAN_APPROVAL.value == "plan_approval"
        assert ProtocolType.SHUTDOWN.value == "shutdown"
        assert ProtocolType.TAKEOVER.value == "takeover"
        assert ProtocolType.BUDGET_CHECK.value == "budget_check"
        assert ProtocolType.POLICY_CHECK.value == "policy_check"

    def test_request_status_values(self):
        assert RequestStatus.PENDING.value == "pending"
        assert RequestStatus.APPROVED.value == "approved"
        assert RequestStatus.REJECTED.value == "rejected"

    def test_protocol_request_dataclass(self):
        request = ProtocolRequest(
            request_id="abc123",
            protocol_type=ProtocolType.PLAN_APPROVAL,
            from_role="PM",
            to_role="QA",
            content={"plan": "v2"},
        )
        assert request.request_id == "abc123"
        assert request.status == RequestStatus.PENDING
        assert request.metadata == {}


class TestCreateProtocolFactory:
    """Tests for factory functions."""

    def test_create_protocol_fsm_default(self):
        fsm = create_protocol_fsm()
        assert isinstance(fsm, ProtocolFSM)

    def test_create_protocol_fsm_with_workspace(self, tmp_path):
        fsm = create_protocol_fsm(workspace=str(tmp_path))
        assert isinstance(fsm, ProtocolFSM)

    def test_create_protocol_bus_default(self):
        bus = create_protocol_bus()
        assert isinstance(bus, ProtocolBus)

    def test_create_protocol_bus_with_workspace(self, tmp_path):
        bus = create_protocol_bus(workspace=str(tmp_path))
        assert isinstance(bus, ProtocolBus)