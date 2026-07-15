"""Tests for polaris.cells.runtime.task_runtime.public.contracts."""

from __future__ import annotations

from typing import Final, cast, get_type_hints

import pytest
from polaris.cells.runtime.task_runtime.public.contracts import (
    DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
    TASK_RUNTIME_EXECUTION_SOURCE_V1,
    TASK_RUNTIME_EXECUTION_STREAM_V1,
    CreateRuntimeTaskCommandV1,
    DirectedEffectParentBindingV1,
    DirectedEffectParentRegistryIdentityV1,
    DirectedEffectStreamEnrollmentResultV1,
    EnrollDirectedEffectOperationStreamCommandV1,
    EnrollDirectedEffectParentRegistryStreamCommandV1,
    GetRuntimeTaskQueryV1,
    HeartbeatTaskRuntimeExecutionAttemptCommandV1,
    ListRuntimeTasksQueryV1,
    OwnerReworkExecutionPreparationCodeV1,
    OwnerReworkExecutionPreparationResultV1,
    ParentCorrelationV1,
    ReopenRuntimeTaskCommandV1,
    RuntimeTaskLifecycleEventV1,
    RuntimeTaskResultV1,
    RuntimeTaskRuntimeError,
    TaskRuntimeExecutionAttemptHeartbeatVerdictV1,
    TaskRuntimeExecutionAttemptIdentityV1,
    TaskRuntimeExecutionAttemptSettlementCodeV1,
    TaskRuntimeExecutionAttemptSettlementVerdictV1,
    TaskRuntimeExecutionAttemptValidationVerdictV1,
    UpdateRuntimeTaskCommandV1,
    ValidateTaskRuntimeExecutionAttemptQueryV1,
)


class TestDirectedEffectStreamEnrollmentContracts:
    """Explicit dynamic-stream enrollment has one strict public boundary."""

    @staticmethod
    def _identity() -> TaskRuntimeExecutionAttemptIdentityV1:
        return TaskRuntimeExecutionAttemptIdentityV1(
            workspace="/tmp/deo-enrollment",
            task_id=17,
            external_task_id="DEO-17",
            session_id="session-17",
            attempt=1,
            role_id="director",
            worker_id="worker-17",
            run_id="run-17",
            lease_expires_at="2026-07-15T01:00:00+00:00",
        )

    @classmethod
    def _binding(cls) -> DirectedEffectParentBindingV1:
        return DirectedEffectParentBindingV1(
            schema_version=DIRECTED_EFFECT_PARENT_BINDING_SCHEMA_V1,
            registry_identity=DirectedEffectParentRegistryIdentityV1.from_execution_attempt(cls._identity()),
            registry_stream_token="task-runtime.deo-parent-registry.17",
            registry_version=1,
            parent_sequence=1,
            binding_id="binding-17",
            operation_stream_token="task-runtime.deo-operation.17",
            binding_hash="b" * 64,
            admission_idempotency_key="parent-17",
            correlation=ParentCorrelationV1(turn_id="turn-17", batch_id="batch-17"),
            actor="contract-test",
            source_event_id="event-17",
            source_event_seq=1,
        )

    def test_commands_require_complete_typed_authority(self) -> None:
        identity = self._identity()
        binding = self._binding()

        assert EnrollDirectedEffectParentRegistryStreamCommandV1(identity).execution_attempt == identity
        operation = EnrollDirectedEffectOperationStreamCommandV1(identity, binding)
        assert operation.execution_attempt == identity
        assert operation.parent_binding == binding
        with pytest.raises(TypeError, match="execution_attempt"):
            EnrollDirectedEffectParentRegistryStreamCommandV1(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="parent_binding"):
            EnrollDirectedEffectOperationStreamCommandV1(identity, object())  # type: ignore[arg-type]

    def test_result_enforces_success_receipt_and_binding_boundaries(self) -> None:
        identity = self._identity()
        binding = self._binding()
        receipt = {"operation": "enroll", "streams": (binding.operation_stream_token,)}

        parent = DirectedEffectStreamEnrollmentResultV1(
            ok=True,
            code="parent_registry_stream_enrolled",
            execution_attempt=identity,
            receipt=receipt,
        )
        operation = DirectedEffectStreamEnrollmentResultV1(
            ok=True,
            code="operation_stream_enrolled",
            execution_attempt=identity,
            parent_binding=binding,
            receipt=receipt,
        )
        assert parent.parent_binding is None
        assert operation.parent_binding == binding
        assert operation.receipt == receipt
        with pytest.raises(ValueError, match="requires a parent binding"):
            DirectedEffectStreamEnrollmentResultV1(
                ok=True,
                code="operation_stream_enrolled",
                execution_attempt=identity,
                receipt=receipt,
            )
        with pytest.raises(ValueError, match="must not carry a parent binding"):
            DirectedEffectStreamEnrollmentResultV1(
                ok=True,
                code="parent_registry_stream_enrolled",
                execution_attempt=identity,
                parent_binding=binding,
                receipt=receipt,
            )
        with pytest.raises(ValueError, match="requires an observational receipt"):
            DirectedEffectStreamEnrollmentResultV1(
                ok=True,
                code="parent_registry_stream_enrolled",
                execution_attempt=identity,
            )

    def test_contract_service_module_and_public_package_exports(self) -> None:
        from polaris.cells.runtime.task_runtime import public
        from polaris.cells.runtime.task_runtime.public import contracts, service

        contract_names = {
            "EnrollDirectedEffectParentRegistryStreamCommandV1",
            "EnrollDirectedEffectOperationStreamCommandV1",
            "DirectedEffectStreamEnrollmentResultV1",
        }
        service_names = {
            "enroll_directed_effect_parent_registry_stream",
            "enroll_directed_effect_operation_stream",
        }
        assert contract_names <= set(contracts.__all__)
        assert service_names <= set(service.__all__)
        assert contract_names | service_names <= set(public.__all__)
        assert (
            public.enroll_directed_effect_parent_registry_stream
            is service.enroll_directed_effect_parent_registry_stream
        )
        assert public.enroll_directed_effect_operation_stream is service.enroll_directed_effect_operation_stream
        with pytest.raises(TypeError, match="EnrollDirectedEffectParentRegistryStreamCommandV1"):
            service.enroll_directed_effect_parent_registry_stream(object())  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="EnrollDirectedEffectOperationStreamCommandV1"):
            service.enroll_directed_effect_operation_stream(object())  # type: ignore[arg-type]


def test_settlement_contract_accepts_all_deo_pre_barrier_refusals() -> None:
    identity = TestDirectedEffectStreamEnrollmentContracts._identity()
    for code in (
        "settlement_parent_close_required",
        "settlement_parent_close_proof_required",
        "settlement_parent_registry_invalid",
        "settlement_parent_registry_unavailable",
    ):
        verdict = TaskRuntimeExecutionAttemptSettlementVerdictV1(
            success=False,
            code=cast(TaskRuntimeExecutionAttemptSettlementCodeV1, code),
            workspace=identity.workspace,
            identity=identity,
            outcome="completed",
            evidence={"registry_code": code},
        )
        assert verdict.code == code
        assert verdict.success is False


class TestTaskRuntimeExecutionFactIdentity:
    """TaskRuntime public contracts own the canonical FactStream identity."""

    def test_identity_values_are_stable(self) -> None:
        assert TASK_RUNTIME_EXECUTION_STREAM_V1 == "task_runtime.execution"
        assert TASK_RUNTIME_EXECUTION_SOURCE_V1 == "runtime.task_runtime"

    def test_identity_constants_are_final_strings(self) -> None:
        from polaris.cells.runtime.task_runtime.public import contracts as mod

        annotations = get_type_hints(mod)
        assert annotations["TASK_RUNTIME_EXECUTION_STREAM_V1"] == Final[str]
        assert annotations["TASK_RUNTIME_EXECUTION_SOURCE_V1"] == Final[str]

    def test_identity_is_exported_from_public_package(self) -> None:
        from polaris.cells.runtime.task_runtime import public

        assert public.TASK_RUNTIME_EXECUTION_STREAM_V1 == TASK_RUNTIME_EXECUTION_STREAM_V1
        assert public.TASK_RUNTIME_EXECUTION_SOURCE_V1 == TASK_RUNTIME_EXECUTION_SOURCE_V1
        assert "TASK_RUNTIME_EXECUTION_STREAM_V1" in public.__all__
        assert "TASK_RUNTIME_EXECUTION_SOURCE_V1" in public.__all__


class TestTaskRuntimeExecutionAttemptIdentity:
    """TaskRuntime owns the durable execution-attempt authority contract."""

    def _identity(self) -> TaskRuntimeExecutionAttemptIdentityV1:
        return TaskRuntimeExecutionAttemptIdentityV1(
            workspace="/tmp/workspace",
            task_id=41,
            external_task_id="TASK-41",
            session_id="tx-41",
            attempt=2,
            role_id="director",
            worker_id="director-worker",
            run_id="run-41",
            lease_expires_at="2026-07-14T00:05:00+00:00",
        )

    def test_identity_is_frozen_and_serializable(self) -> None:
        identity = self._identity()

        assert identity.to_record() == {
            "schema_version": "task-runtime.execution-attempt-identity/1",
            "workspace": "/tmp/workspace",
            "task_id": 41,
            "external_task_id": "TASK-41",
            "session_id": "tx-41",
            "attempt": 2,
            "role_id": "director",
            "worker_id": "director-worker",
            "run_id": "run-41",
            "lease_expires_at": "2026-07-14T00:05:00+00:00",
        }
        with pytest.raises(AttributeError):
            identity.session_id = "forged"  # type: ignore[misc]

    def test_from_record_requires_exact_canonical_schema(self) -> None:
        identity = self._identity()

        assert TaskRuntimeExecutionAttemptIdentityV1.from_record(identity.to_record()) == identity
        with pytest.raises(ValueError, match="fields must match canonical schema"):
            TaskRuntimeExecutionAttemptIdentityV1.from_record({**identity.to_record(), "legacy_session_id": "tx-41"})
        with pytest.raises(TypeError, match="task_id must be an int"):
            TaskRuntimeExecutionAttemptIdentityV1.from_record({**identity.to_record(), "task_id": "41"})
        with pytest.raises(ValueError, match="schema_version is unsupported"):
            TaskRuntimeExecutionAttemptIdentityV1.from_record({**identity.to_record(), "schema_version": "legacy/1"})

    def test_query_and_verdict_require_typed_identity(self) -> None:
        identity = self._identity()
        query = ValidateTaskRuntimeExecutionAttemptQueryV1(
            workspace="/tmp/workspace",
            identity=identity,
        )
        verdict = TaskRuntimeExecutionAttemptValidationVerdictV1(
            valid=True,
            code="valid",
            workspace=query.workspace,
            identity=identity,
            evidence={"observed": identity.to_record()},
        )

        assert verdict.to_record()["identity"] == identity.to_record()
        with pytest.raises(ValueError, match="valid must match"):
            TaskRuntimeExecutionAttemptValidationVerdictV1(
                valid=True,
                code="session_mismatch",
                workspace=query.workspace,
                identity=identity,
            )

    def test_attempt_authority_contracts_are_publicly_exported(self) -> None:
        from polaris.cells.runtime.task_runtime import public

        assert public.TaskRuntimeExecutionAttemptIdentityV1 is TaskRuntimeExecutionAttemptIdentityV1
        assert public.ValidateTaskRuntimeExecutionAttemptQueryV1 is ValidateTaskRuntimeExecutionAttemptQueryV1
        assert public.TaskRuntimeExecutionAttemptValidationVerdictV1 is TaskRuntimeExecutionAttemptValidationVerdictV1
        assert "TaskRuntimeExecutionAttemptIdentityV1" in public.__all__
        assert "ValidateTaskRuntimeExecutionAttemptQueryV1" in public.__all__
        assert "TaskRuntimeExecutionAttemptValidationVerdictV1" in public.__all__

    def test_error_code_contracts_stay_separate(self) -> None:
        """Owner-rework, heartbeat, and validation results keep distinct code domains."""

        owner_rework_hints = get_type_hints(OwnerReworkExecutionPreparationResultV1)
        heartbeat_hints = get_type_hints(TaskRuntimeExecutionAttemptHeartbeatVerdictV1)
        validation_hints = get_type_hints(TaskRuntimeExecutionAttemptValidationVerdictV1)

        assert owner_rework_hints["code"] == OwnerReworkExecutionPreparationCodeV1
        assert owner_rework_hints["code"] != heartbeat_hints["code"]
        assert owner_rework_hints["code"] != validation_hints["code"]
        from polaris.cells.runtime.task_runtime import public

        assert public.OwnerReworkExecutionPreparationCodeV1 is OwnerReworkExecutionPreparationCodeV1
        assert "OwnerReworkExecutionPreparationCodeV1" in public.__all__

    def test_bounded_heartbeat_contract_requires_canonical_identity_and_verdict(self) -> None:
        from polaris.cells.runtime.task_runtime import public

        identity = self._identity()
        command = HeartbeatTaskRuntimeExecutionAttemptCommandV1(
            workspace="/tmp/workspace",
            identity=identity,
            lease_ttl_seconds=30,
            lock_timeout_seconds=0.25,
            context_summary="contract-test",
        )
        verdict = TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
            success=True,
            code="heartbeat_renewed",
            workspace=command.workspace,
            identity=identity,
            renewed_identity=identity,
            evidence_anchor={"session_write_receipt": {"after_hash": "abc"}},
        )

        assert command.to_record()["identity"] == identity.to_record()
        assert verdict.to_record()["reason"] == "heartbeat_renewed"
        with pytest.raises(ValueError, match="finite number >= 0"):
            HeartbeatTaskRuntimeExecutionAttemptCommandV1(
                workspace="/tmp/workspace",
                identity=identity,
                lease_ttl_seconds=30,
                lock_timeout_seconds=-0.1,
            )
        with pytest.raises(ValueError, match="requires renewed_identity"):
            TaskRuntimeExecutionAttemptHeartbeatVerdictV1(
                success=True,
                code="heartbeat_renewed",
                workspace="/tmp/workspace",
                identity=identity,
            )
        assert public.HeartbeatTaskRuntimeExecutionAttemptCommandV1 is HeartbeatTaskRuntimeExecutionAttemptCommandV1
        assert public.TaskRuntimeExecutionAttemptHeartbeatVerdictV1 is TaskRuntimeExecutionAttemptHeartbeatVerdictV1
        assert "HeartbeatTaskRuntimeExecutionAttemptCommandV1" in public.__all__
        assert "TaskRuntimeExecutionAttemptHeartbeatVerdictV1" in public.__all__


class TestRequireNonEmptyHelper:
    """Tests for the internal _require_non_empty helper (via public API)."""

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="", workspace="ws", title="title", owner="owner")

    def test_whitespace_only_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="   ", workspace="ws", title="title", owner="owner")

    def test_none_converted_to_string_raises(self) -> None:
        # str(None) returns "None" which is non-empty, so this should succeed
        cmd = CreateRuntimeTaskCommandV1(
            task_id=None,  # type: ignore[arg-type]
            workspace="ws",
            title="title",
            owner="owner",
        )
        assert cmd.task_id == "None"


class TestCreateRuntimeTaskCommandV1:
    """Tests for CreateRuntimeTaskCommandV1."""

    def test_create_with_all_fields(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            title="Test Task",
            owner="user-001",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.title == "Test Task"
        assert cmd.owner == "user-001"
        assert cmd.payload == {}

    def test_create_with_payload(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            title="Test",
            owner="user-001",
            payload={"key": "value"},
        )
        assert cmd.payload == {"key": "value"}

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="", title="Test", owner="user-001")

    def test_empty_title_raises(self) -> None:
        with pytest.raises(ValueError, match="title must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", title="", owner="user-001")

    def test_empty_owner_raises(self) -> None:
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            CreateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", title="Test", owner="")

    def test_is_frozen(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        with pytest.raises(AttributeError):
            cmd.task_id = "x"  # type: ignore[misc]

    def test_payload_defaults_to_empty_dict(self) -> None:
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        assert cmd.payload == {}

    def test_payload_is_copied(self) -> None:
        original = {"key": "value"}
        cmd = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o", payload=original)
        assert cmd.payload is not original

    def test_equality(self) -> None:
        cmd1 = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        cmd2 = CreateRuntimeTaskCommandV1(task_id="t", workspace="w", title="t", owner="o")
        assert cmd1 == cmd2


class TestUpdateRuntimeTaskCommandV1:
    """Tests for UpdateRuntimeTaskCommandV1."""

    def test_create_with_all_fields(self) -> None:
        cmd = UpdateRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.status == "completed"

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status must be a non-empty string"):
            UpdateRuntimeTaskCommandV1(task_id="task-001", workspace="ws", status="")

    def test_payload_defaults_to_empty_dict(self) -> None:
        cmd = UpdateRuntimeTaskCommandV1(task_id="t", workspace="w", status="s")
        assert cmd.payload == {}


class TestReopenRuntimeTaskCommandV1:
    """Tests for ReopenRuntimeTaskCommandV1."""

    def test_create(self) -> None:
        cmd = ReopenRuntimeTaskCommandV1(
            task_id="task-001",
            workspace="/tmp/ws",
            reason="needs more work",
        )
        assert cmd.task_id == "task-001"
        assert cmd.workspace == "/tmp/ws"
        assert cmd.reason == "needs more work"

    def test_empty_reason_raises(self) -> None:
        with pytest.raises(ValueError, match="reason must be a non-empty string"):
            ReopenRuntimeTaskCommandV1(task_id="task-001", workspace="ws", reason="")

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            ReopenRuntimeTaskCommandV1(task_id="", workspace="ws", reason="r")


class TestListRuntimeTasksQueryV1:
    """Tests for ListRuntimeTasksQueryV1."""

    def test_create_with_defaults(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="/tmp/ws")
        assert query.workspace == "/tmp/ws"
        assert query.statuses == ()
        assert query.owner is None
        assert query.limit == 100
        assert query.offset == 0

    def test_create_with_all_fields(self) -> None:
        query = ListRuntimeTasksQueryV1(
            workspace="/tmp/ws",
            statuses=("pending", "completed"),
            owner="user-001",
            limit=50,
            offset=10,
        )
        assert query.workspace == "/tmp/ws"
        assert query.statuses == ("pending", "completed")
        assert query.owner == "user-001"
        assert query.limit == 50
        assert query.offset == 10

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            ListRuntimeTasksQueryV1(workspace="")

    def test_limit_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ListRuntimeTasksQueryV1(workspace="ws", limit=0)

    def test_limit_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="limit must be >= 1"):
            ListRuntimeTasksQueryV1(workspace="ws", limit=-1)

    def test_offset_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="offset must be >= 0"):
            ListRuntimeTasksQueryV1(workspace="ws", offset=-1)

    def test_statuses_filtered(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", statuses=("pending", "", "completed", "  "))
        assert query.statuses == ("pending", "completed")

    def test_owner_none_allowed(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", owner=None)
        assert query.owner is None

    def test_owner_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="owner must be a non-empty string"):
            ListRuntimeTasksQueryV1(workspace="ws", owner="")

    def test_large_limit(self) -> None:
        query = ListRuntimeTasksQueryV1(workspace="ws", limit=10000)
        assert query.limit == 10000


class TestGetRuntimeTaskQueryV1:
    """Tests for GetRuntimeTaskQueryV1."""

    def test_create(self) -> None:
        query = GetRuntimeTaskQueryV1(task_id="task-001", workspace="/tmp/ws")
        assert query.task_id == "task-001"
        assert query.workspace == "/tmp/ws"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            GetRuntimeTaskQueryV1(task_id="", workspace="ws")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace must be a non-empty string"):
            GetRuntimeTaskQueryV1(task_id="t", workspace="")


class TestRuntimeTaskLifecycleEventV1:
    """Tests for RuntimeTaskLifecycleEventV1."""

    def test_create(self) -> None:
        event = RuntimeTaskLifecycleEventV1(
            event_id="evt-001",
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
            occurred_at="2024-01-01T00:00:00Z",
        )
        assert event.event_id == "evt-001"
        assert event.task_id == "task-001"
        assert event.workspace == "/tmp/ws"
        assert event.status == "completed"
        assert event.occurred_at == "2024-01-01T00:00:00Z"
        assert event.payload == {}

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id must be a non-empty string"):
            RuntimeTaskLifecycleEventV1(event_id="", task_id="t", workspace="w", status="s", occurred_at="t")

    def test_empty_occurred_at_raises(self) -> None:
        with pytest.raises(ValueError, match="occurred_at must be a non-empty string"):
            RuntimeTaskLifecycleEventV1(event_id="e", task_id="t", workspace="w", status="s", occurred_at="")

    def test_payload_defaults_to_empty_dict(self) -> None:
        event = RuntimeTaskLifecycleEventV1(event_id="e", task_id="t", workspace="w", status="s", occurred_at="t")
        assert event.payload == {}


class TestRuntimeTaskResultV1:
    """Tests for RuntimeTaskResultV1."""

    def test_create(self) -> None:
        result = RuntimeTaskResultV1(
            task_id="task-001",
            workspace="/tmp/ws",
            status="completed",
            version=1,
        )
        assert result.task_id == "task-001"
        assert result.workspace == "/tmp/ws"
        assert result.status == "completed"
        assert result.version == 1
        assert result.updated is True

    def test_create_with_updated_false(self) -> None:
        result = RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=0, updated=False)
        assert result.updated is False

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id must be a non-empty string"):
            RuntimeTaskResultV1(task_id="", workspace="w", status="s", version=0)

    def test_negative_version_raises(self) -> None:
        with pytest.raises(ValueError, match="version must be >= 0"):
            RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=-1)

    def test_version_zero_allowed(self) -> None:
        result = RuntimeTaskResultV1(task_id="t", workspace="w", status="s", version=0)
        assert result.version == 0


class TestRuntimeTaskRuntimeError:
    """Tests for RuntimeTaskRuntimeError exception."""

    def test_create_with_message(self) -> None:
        err = RuntimeTaskRuntimeError("something went wrong")
        assert str(err) == "something went wrong"
        assert err.code == "runtime_task_runtime_error"
        assert err.details == {}

    def test_create_with_custom_code(self) -> None:
        err = RuntimeTaskRuntimeError("msg", code="custom_code")
        assert err.code == "custom_code"

    def test_create_with_details(self) -> None:
        err = RuntimeTaskRuntimeError("msg", details={"key": "value"})
        assert err.details == {"key": "value"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message must be a non-empty string"):
            RuntimeTaskRuntimeError("")

    def test_is_runtime_error(self) -> None:
        err = RuntimeTaskRuntimeError("test")
        assert isinstance(err, RuntimeError)

    def test_raise_and_catch(self) -> None:
        with pytest.raises(RuntimeTaskRuntimeError) as exc_info:
            raise RuntimeTaskRuntimeError("test error")
        assert str(exc_info.value) == "test error"

    def test_details_is_copied(self) -> None:
        original = {"key": "value"}
        err = RuntimeTaskRuntimeError("msg", details=original)
        assert err.details is not original


class TestModuleExports:
    """Tests for module __all__ exports."""

    def test_all_exports_present(self) -> None:
        from polaris.cells.runtime.task_runtime.public import contracts as mod

        assert hasattr(mod, "__all__")
        assert "CreateRuntimeTaskCommandV1" in mod.__all__
        assert "GetRuntimeTaskQueryV1" in mod.__all__
        assert "ListRuntimeTasksQueryV1" in mod.__all__
        assert "ReopenRuntimeTaskCommandV1" in mod.__all__
        assert "RuntimeTaskLifecycleEventV1" in mod.__all__
        assert "RuntimeTaskResultV1" in mod.__all__
        assert "RuntimeTaskRuntimeError" in mod.__all__
        assert "TASK_RUNTIME_EXECUTION_SOURCE_V1" in mod.__all__
        assert "TASK_RUNTIME_EXECUTION_STREAM_V1" in mod.__all__
        assert "UpdateRuntimeTaskCommandV1" in mod.__all__
        assert len(mod.__all__) == len(set(mod.__all__))
        assert all(hasattr(mod, name) for name in mod.__all__)
