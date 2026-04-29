"""Tests for polaris.domain.entities module."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from polaris.domain.entities.capability import (
    DEFAULT_ROLE_CAPABILITIES,
    Capability,
    CapabilityChecker,
    Role,
    RoleConfig,
    Skill,
    check_action_allowed,
    get_role_capabilities,
    get_role_config,
    validate_director_action,
)
from polaris.domain.entities.defect import DEFAULT_DEFECT_TICKET_FIELDS
from polaris.domain.entities.evidence_bundle import (
    ChangeType,
    EvidenceBundle,
    FileChange,
    PerfEvidence,
    SourceType,
    StaticAnalysisEvidence,
    TestRunEvidence,
)
from polaris.domain.entities.export_artifacts import (
    ExportFormat,
    ExportType,
    PMTaskDraft,
    RoleSessionExport,
    create_execution_notes,
    create_patch_summary,
    create_plan_notes,
    create_pm_task_draft,
    create_qa_audit_draft,
    parse_export_type,
)
from polaris.domain.entities.policy import (
    Policy,
)
from polaris.domain.entities.task import (
    Task,
    TaskEvidence,
    TaskPriority,
    TaskResult,
    TaskStateError,
    TaskStatus,
)
from polaris.domain.entities.worker import (
    Worker,
    WorkerCapabilities,
    WorkerHealth,
    WorkerStateError,
    WorkerStatus,
    WorkerType,
)
from polaris.domain.entities.workflow import (
    DirectorWorkflowResult,
    ExecutionMode,
    PMWorkflowResult,
    _coerce_execution_mode,
    _coerce_positive_int,
)

# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------


class TestTaskCreation:
    def test_default_creation(self):
        t = Task(id=1, subject="test task")
        assert t.id == 1
        assert t.subject == "test task"
        assert t.status == TaskStatus.PENDING
        assert t.priority == TaskPriority.MEDIUM
        assert t.description == ""
        assert t.blocked_by == []

    def test_creation_with_all_fields(self):
        t = Task(
            id="task-1",
            subject="full task",
            description="desc",
            status=TaskStatus.READY,
            priority=TaskPriority.HIGH,
            blocked_by=[2, 3],
            command="python test.py",
            max_retries=5,
        )
        assert t.id == "task-1"
        assert t.status == TaskStatus.READY
        assert t.priority == TaskPriority.HIGH
        assert t.blocked_by == [2, 3]
        assert t.max_retries == 5


class TestTaskStatusProperties:
    def test_terminal_states(self):
        assert TaskStatus.COMPLETED.is_terminal is True
        assert TaskStatus.FAILED.is_terminal is True
        assert TaskStatus.CANCELLED.is_terminal is True
        assert TaskStatus.TIMEOUT.is_terminal is True
        assert TaskStatus.PENDING.is_terminal is False
        assert TaskStatus.IN_PROGRESS.is_terminal is False

    def test_active_states(self):
        assert TaskStatus.QUEUED.is_active is True
        assert TaskStatus.PENDING.is_active is True
        assert TaskStatus.READY.is_active is True
        assert TaskStatus.CLAIMED.is_active is True
        assert TaskStatus.BLOCKED.is_active is True
        assert TaskStatus.WAITING_HUMAN.is_active is True
        assert TaskStatus.COMPLETED.is_active is False

    def test_executing_states(self):
        assert TaskStatus.CLAIMED.is_executing is True
        assert TaskStatus.IN_PROGRESS.is_executing is True
        assert TaskStatus.RUNNING.is_executing is True
        assert TaskStatus.READY.is_executing is False

    def test_running_alias(self):
        assert TaskStatus.RUNNING.value == "in_progress"
        assert TaskStatus.RUNNING == TaskStatus.IN_PROGRESS


class TestTaskPriority:
    def test_numeric_values(self):
        assert TaskPriority.LOW.numeric_value == 0
        assert TaskPriority.MEDIUM.numeric_value == 1
        assert TaskPriority.HIGH.numeric_value == 2
        assert TaskPriority.CRITICAL.numeric_value == 3


class TestTaskStateTransitions:
    def test_mark_ready(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert t.status == TaskStatus.READY

    def test_mark_ready_invalid(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        with pytest.raises(TaskStateError):
            t.mark_ready()

    def test_claim(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        t.claim("worker1")
        assert t.status == TaskStatus.CLAIMED
        assert t.claimed_by == "worker1"
        assert t.claimed_at is not None

    def test_claim_not_ready(self):
        t = Task(id=1, subject="test")
        with pytest.raises(TaskStateError):
            t.claim("worker1")

    def test_start(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        t.claim("worker1")
        t.start()
        assert t.status == TaskStatus.IN_PROGRESS
        assert t.started_at is not None

    def test_start_not_claimed(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        with pytest.raises(TaskStateError):
            t.start()

    def test_complete_success(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        t.claim("worker1")
        t.start()
        result = TaskResult(success=True, output="done")
        t.complete(result)
        assert t.status == TaskStatus.COMPLETED
        assert t.result_summary == "done"
        assert t.completed_at is not None

    def test_complete_failure_with_retry(self):
        t = Task(id=1, subject="test", max_retries=2)
        t.mark_ready()
        t.claim("worker1")
        t.start()
        result = TaskResult(success=False, output="failed")
        t.complete(result)
        assert t.status == TaskStatus.READY
        assert t.retry_count == 1
        assert t.claimed_by is None

    def test_complete_failure_exhausted(self):
        t = Task(id=1, subject="test", max_retries=1)
        t.mark_ready()
        t.claim("worker1")
        t.start()
        t.complete(TaskResult(success=False))
        t.claim("worker1")
        t.start()
        t.complete(TaskResult(success=False))
        assert t.status == TaskStatus.FAILED

    def test_complete_invalid_state(self):
        t = Task(id=1, subject="test")
        with pytest.raises(TaskStateError):
            t.complete(TaskResult(success=True))

    def test_cancel(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        t.cancel()
        assert t.status == TaskStatus.CANCELLED
        assert t.completed_at is not None

    def test_cancel_terminal_raises(self):
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        with pytest.raises(TaskStateError):
            t.cancel()

    def test_timeout(self):
        t = Task(id=1, subject="test")
        t.mark_ready()
        t.claim("worker1")
        t.start()
        t.timeout_task()
        assert t.status == TaskStatus.TIMEOUT
        assert t.error_message == "Execution exceeded timeout limit"

    def test_timeout_terminal_raises(self):
        t = Task(id=1, subject="test", status=TaskStatus.FAILED)
        with pytest.raises(TaskStateError):
            t.timeout_task()

    def test_reopen(self):
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        t.reopen()
        assert t.status == TaskStatus.PENDING
        assert t.claimed_by is None
        assert t.completed_at is None

    def test_reopen_blocked(self):
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED, blocked_by=[2])
        t.reopen()
        assert t.status == TaskStatus.BLOCKED

    def test_reopen_non_terminal_raises(self):
        t = Task(id=1, subject="test", status=TaskStatus.PENDING)
        with pytest.raises(TaskStateError):
            t.reopen()

    def test_resolve_dependency(self):
        t = Task(id=1, subject="test", blocked_by=[2, 3])
        t.resolve_dependency(2)
        assert t.blocked_by == [3]
        t.resolve_dependency(999)
        assert t.blocked_by == [3]


class TestTaskComputedProperties:
    def test_is_terminal(self):
        t = Task(id=1, subject="test", status=TaskStatus.COMPLETED)
        assert t.is_terminal is True

    def test_is_blocked(self):
        t = Task(id=1, subject="test", blocked_by=[2], status=TaskStatus.PENDING)
        assert t.is_blocked is True

    def test_is_blocked_not_blocked(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        assert t.is_blocked is False

    def test_is_claimable(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        assert t.is_claimable is True

    def test_is_claimable_when_blocked(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY, blocked_by=[2])
        assert t.is_claimable is False

    def test_result_property(self):
        t = Task(id=1, subject="test")
        assert t.result is None


class TestTaskSerialization:
    def test_to_dict(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        d = t.to_dict()
        assert d["id"] == 1
        assert d["status"] == "ready"
        assert d["priority_numeric"] == 1

    def test_from_dict_basic(self):
        d = {"id": 1, "subject": "test", "status": "in_progress"}
        t = Task.from_dict(d)
        assert t.id == 1
        assert t.status == TaskStatus.IN_PROGRESS

    def test_from_dict_with_taskstatus(self):
        d = {"id": 1, "subject": "test", "status": TaskStatus.COMPLETED}
        t = Task.from_dict(d)
        assert t.status == TaskStatus.COMPLETED

    def test_from_dict_invalid_status_fallback(self):
        d = {"id": 1, "subject": "test", "status": "invalid"}
        t = Task.from_dict(d)
        assert t.status == TaskStatus.PENDING

    def test_from_dict_legacy_blockedby(self):
        d = {"id": 1, "subject": "test", "blockedBy": [2, 3]}
        t = Task.from_dict(d)
        assert t.blocked_by == [2, 3]

    def test_from_dict_float_id(self):
        d = {"id": 1.5, "subject": "test"}
        t = Task.from_dict(d)
        assert t.id == 1
        assert isinstance(t.id, int)

    def test_from_dict_str_id(self):
        d = {"id": "task-1", "subject": "test"}
        t = Task.from_dict(d)
        assert t.id == "task-1"

    def test_roundtrip(self):
        t = Task(id=1, subject="test", status=TaskStatus.READY)
        d = t.to_dict()
        t2 = Task.from_dict(d)
        assert t2.id == t.id
        assert t2.subject == t.subject
        assert t2.status == t.status


class TestTaskResult:
    def test_creation(self):
        r = TaskResult(success=True, output="done", exit_code=0)
        assert r.success is True
        assert r.output == "done"

    def test_to_dict(self):
        ev = TaskEvidence(type="file", path="/tmp/out.txt")
        r = TaskResult(success=True, evidence=(ev,))
        d = r.to_dict()
        assert d["success"] is True
        assert len(d["evidence"]) == 1
        assert d["evidence"][0]["type"] == "file"

    def test_from_dict(self):
        d = {
            "success": True,
            "output": "done",
            "exit_code": 0,
            "duration_ms": 100,
            "evidence": [{"type": "log", "path": None, "content": "log line", "metadata": {}}],
        }
        r = TaskResult.from_dict(d)
        assert r.success is True
        assert len(r.evidence) == 1
        assert r.evidence[0].type == "log"


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


class TestWorkerCreation:
    def test_defaults(self):
        w = Worker(id="w1", name="worker1")
        assert w.status == WorkerStatus.IDLE
        assert w.worker_type == WorkerType.LOCAL
        assert w.current_task_id is None
        assert w.max_concurrent_tasks == 1

    def test_custom_capabilities(self):
        caps = WorkerCapabilities(can_execute_bash=False, supported_languages=["python"])
        w = Worker(id="w1", name="worker1", capabilities=caps)
        assert w.capabilities.can_execute_bash is False
        assert w.capabilities.supported_languages == ["python"]


class TestWorkerAvailability:
    def test_is_available_idle(self):
        w = Worker(id="w1", name="worker1")
        assert w.is_available() is True

    def test_is_available_busy(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        assert w.is_available() is False

    def test_is_available_failed(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.FAILED)
        assert w.is_available() is False


class TestWorkerTaskLifecycle:
    def test_claim_task(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        assert w.status == WorkerStatus.BUSY
        assert w.current_task_id == "task-1"
        assert w.started_at is not None

    def test_claim_task_unavailable(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        with pytest.raises(WorkerStateError):
            w.claim_task("task-1")

    def test_release_task_success(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        result = TaskResult(success=True, duration_ms=1000)
        w.release_task(result)
        assert w.status == WorkerStatus.IDLE
        assert w.current_task_id is None
        assert w.health.tasks_completed == 1
        assert w.health.total_execution_time_ms == 1000

    def test_release_task_failure(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        result = TaskResult(success=False, duration_ms=500)
        w.release_task(result)
        assert w.health.tasks_failed == 1
        assert w.health.consecutive_failures == 1

    def test_release_not_busy(self):
        w = Worker(id="w1", name="worker1")
        with pytest.raises(WorkerStateError):
            w.release_task(TaskResult(success=True))

    def test_update_heartbeat(self):
        w = Worker(id="w1", name="worker1")
        old_heartbeat = w.health.last_heartbeat
        w.update_heartbeat()
        assert w.health.last_heartbeat >= old_heartbeat

    def test_mark_failed(self):
        w = Worker(id="w1", name="worker1")
        w.mark_failed("oom")
        assert w.status == WorkerStatus.FAILED
        assert w.metadata["failure_reason"] == "oom"
        assert w.stopped_at is not None

    def test_request_stop_idle(self):
        w = Worker(id="w1", name="worker1")
        w.request_stop()
        assert w.status == WorkerStatus.STOPPED
        assert w.stopped_at is not None

    def test_request_stop_busy(self):
        w = Worker(id="w1", name="worker1")
        w.claim_task("task-1")
        w.request_stop()
        assert w.status == WorkerStatus.STOPPING


class TestWorkerCanAcceptTask:
    def test_basic_task(self):
        w = Worker(id="w1", name="worker1")
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert w.can_accept_task(t) is True

    def test_bash_task_rejected(self):
        w = Worker(id="w1", name="worker1", capabilities=WorkerCapabilities(can_execute_bash=False))
        t = Task(id=1, subject="test", command="bash script.sh")
        t.mark_ready()
        assert w.can_accept_task(t) is False

    def test_unavailable_worker(self):
        w = Worker(id="w1", name="worker1", status=WorkerStatus.BUSY)
        t = Task(id=1, subject="test")
        t.mark_ready()
        assert w.can_accept_task(t) is False


class TestWorkerSerialization:
    def test_to_dict(self):
        w = Worker(id="w1", name="worker1")
        d = w.to_dict()
        assert d["id"] == "w1"
        assert d["status"] == "IDLE"
        assert d["health"]["is_healthy"] is True
        assert "last_heartbeat" in d["health"]


class TestWorkerHealth:
    def test_is_healthy(self):
        h = WorkerHealth()
        assert h.is_healthy(60) is True

    def test_is_healthy_timeout(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        h = WorkerHealth(last_heartbeat=old)
        assert h.is_healthy(60) is False

    def test_with_updates(self):
        h = WorkerHealth(tasks_completed=0)
        h2 = h.with_updates(tasks_completed=5)
        assert h2.tasks_completed == 5
        assert h.tasks_completed == 0


# ---------------------------------------------------------------------------
# EvidenceBundle
# ---------------------------------------------------------------------------


class TestFileChange:
    def test_creation(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10)
        assert fc.path == "a.py"
        assert fc.change_type == ChangeType.ADDED
        assert fc.lines_added == 10

    def test_to_dict(self):
        fc = FileChange(path="a.py", change_type=ChangeType.MODIFIED)
        d = fc.to_dict()
        assert d["path"] == "a.py"
        assert d["change_type"] == "modified"

    def test_from_dict(self):
        d = {"path": "b.py", "change_type": "deleted", "lines_added": 5}
        fc = FileChange.from_dict(d)
        assert fc.path == "b.py"
        assert fc.change_type == ChangeType.DELETED
        assert fc.lines_added == 5

    def test_is_large_patch_true(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch="x" * 200000)
        assert fc.is_large_patch is True

    def test_is_large_patch_false(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, patch="small")
        assert fc.is_large_patch is False

    def test_is_large_patch_none(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        assert fc.is_large_patch is False


class TestTestRunEvidence:
    def test_creation(self):
        tre = TestRunEvidence(
            test_command="pytest",
            exit_code=0,
            total_tests=10,
            passed=10,
            failed=0,
            skipped=0,
            duration_seconds=5.0,
        )
        assert tre.test_command == "pytest"
        assert tre.exit_code == 0

    def test_to_dict(self):
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=5, passed=5, failed=0, skipped=0, duration_seconds=1.0
        )
        d = tre.to_dict()
        assert d["test_command"] == "pytest"
        assert d["passed"] == 5

    def test_from_dict(self):
        d = {
            "test_command": "pytest",
            "exit_code": 1,
            "total_tests": 5,
            "passed": 4,
            "failed": 1,
            "skipped": 0,
            "duration_seconds": 2.0,
        }
        tre = TestRunEvidence.from_dict(d)
        assert tre.exit_code == 1


class TestPerfEvidence:
    def test_creation(self):
        pe = PerfEvidence(benchmark_command="bench", metrics={"time": 1.5})
        assert pe.benchmark_command == "bench"
        assert pe.metrics == {"time": 1.5}

    def test_roundtrip(self):
        pe = PerfEvidence(benchmark_command="bench", metrics={"time": 1.5})
        d = pe.to_dict()
        pe2 = PerfEvidence.from_dict(d)
        assert pe2.metrics == {"time": 1.5}


class TestStaticAnalysisEvidence:
    def test_creation(self):
        sae = StaticAnalysisEvidence(tool_name="ruff", issues=[{"msg": "error"}])
        assert sae.tool_name == "ruff"
        assert len(sae.issues) == 1

    def test_roundtrip(self):
        sae = StaticAnalysisEvidence(tool_name="mypy")
        d = sae.to_dict()
        sae2 = StaticAnalysisEvidence.from_dict(d)
        assert sae2.tool_name == "mypy"
        assert sae2.issues == []


class TestEvidenceBundle:
    def test_creation(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10, lines_deleted=2)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
            source_type=SourceType.MANUAL,
        )
        assert eb.bundle_id == "b1"
        assert len(eb.change_set) == 1

    def test_total_lines_changed(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10, lines_deleted=2)
        fc2 = FileChange(path="b.py", change_type=ChangeType.MODIFIED, lines_added=5, lines_deleted=3)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc1, fc2],
            source_type=SourceType.MANUAL,
        )
        assert eb.total_lines_changed == (15, 5)

    def test_affected_files(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
            source_type=SourceType.MANUAL,
        )
        assert eb.affected_files == ["a.py"]

    def test_affected_symbols(self):
        fc1 = FileChange(path="a.py", change_type=ChangeType.ADDED, related_symbols=["foo"])
        fc2 = FileChange(path="b.py", change_type=ChangeType.MODIFIED, related_symbols=["bar", "foo"])
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc1, fc2],
            source_type=SourceType.MANUAL,
        )
        symbols = eb.affected_symbols
        assert len(symbols) == 2
        assert "foo" in symbols
        assert "bar" in symbols

    def test_get_change_for_file(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
            source_type=SourceType.MANUAL,
        )
        assert eb.get_change_for_file("a.py") is not None
        assert eb.get_change_for_file("missing.py") is None

    def test_compute_content_hash(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            head_sha="def",
            change_set=[fc],
            source_type=SourceType.MANUAL,
        )
        h1 = eb.compute_content_hash()
        h2 = eb.compute_content_hash()
        assert h1 == h2
        assert len(h1) == 16

    def test_to_dict(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED)
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
            source_type=SourceType.MANUAL,
        )
        d = eb.to_dict()
        assert d["bundle_id"] == "b1"
        assert d["source_type"] == "manual"
        assert len(d["change_set"]) == 1

    def test_json_roundtrip(self):
        fc = FileChange(path="a.py", change_type=ChangeType.ADDED, lines_added=10)
        tre = TestRunEvidence(
            test_command="pytest", exit_code=0, total_tests=10, passed=10, failed=0, skipped=0, duration_seconds=5.0
        )
        eb = EvidenceBundle(
            bundle_id="b1",
            workspace="/tmp/ws",
            base_sha="abc",
            change_set=[fc],
            test_results=tre,
            source_type=SourceType.MANUAL,
        )
        json_str = eb.to_json()
        eb2 = EvidenceBundle.from_json(json_str)
        assert eb2.bundle_id == "b1"
        assert len(eb2.change_set) == 1
        assert eb2.test_results.exit_code == 0


# ---------------------------------------------------------------------------
# Policy
# ---------------------------------------------------------------------------


class TestPolicyDefaults:
    def test_default_creation(self):
        p = Policy()
        assert p.repair.auto_repair is True
        assert p.repair.max_attempts == 3
        assert p.risk.block_threshold == 7
        assert p.rag.topk == 5
        assert p.memory.backend == "file"
        assert p.budgets.max_tool_rounds == 10

    def test_dict_init(self):
        p = Policy(repair={"auto_repair": False})
        assert p.repair.auto_repair is False
        assert p.repair.max_attempts == 3


class TestPolicyFromDict:
    def test_from_dict_empty(self):
        p = Policy.from_dict({})
        assert p.repair.auto_repair is True

    def test_from_dict_custom(self):
        p = Policy.from_dict({"repair": {"auto_repair": False, "max_attempts": 5}})
        assert p.repair.auto_repair is False
        assert p.repair.max_attempts == 5

    def test_from_dict_none(self):
        p = Policy.from_dict(None)
        assert p.repair.auto_repair is True

    def test_from_dict_invalid(self):
        p = Policy.from_dict("invalid")
        assert p.repair.auto_repair is True


class TestPolicyToDict:
    def test_to_dict(self):
        p = Policy()
        d = p.to_dict()
        assert "repair" in d
        assert "risk" in d
        assert "factory" in d
        assert d["repair"]["auto_repair"] is True

    def test_roundtrip(self):
        p = Policy.from_dict({"repair": {"auto_repair": False}})
        d = p.to_dict()
        assert d["repair"]["auto_repair"] is False


# ---------------------------------------------------------------------------
# Capability
# ---------------------------------------------------------------------------


class TestRoleEnum:
    def test_role_values(self):
        assert Role.DIRECTOR.value == "director"
        assert Role.PM.value == "pm"
        assert Role.QA.value == "qa"


class TestCapabilityEnum:
    def test_capability_values(self):
        assert Capability.READ_FILES.value == "read_files"
        assert Capability.WRITE_FILES.value == "write_files"
        assert Capability.DELETE_FILES.value == "delete_files"


class TestDefaultRoleCapabilities:
    def test_director_has_read(self):
        assert Capability.READ_FILES in DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]

    def test_director_no_delete(self):
        assert Capability.DELETE_FILES not in DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR]

    def test_system_has_all(self):
        caps = DEFAULT_ROLE_CAPABILITIES[Role.SYSTEM]
        assert Capability.DELETE_FILES in caps
        assert Capability.MANAGE_WORKERS in caps
        assert Capability.EXECUTE_TESTS in caps


class TestRoleConfig:
    def test_default_capabilities(self):
        rc = RoleConfig(role=Role.DIRECTOR)
        assert len(rc.capabilities) == 7
        assert rc.has_capability(Capability.READ_FILES) is True

    def test_custom_capabilities(self):
        rc = RoleConfig(role=Role.DIRECTOR, capabilities={Capability.READ_FILES})
        assert rc.has_capability(Capability.READ_FILES) is True
        assert rc.has_capability(Capability.WRITE_FILES) is False

    def test_can_use_tool_allowed(self):
        rc = RoleConfig(role=Role.DIRECTOR, allowed_tools={"read", "write"})
        assert rc.can_use_tool("read") is True
        assert rc.can_use_tool("delete") is False

    def test_can_use_tool_blocked(self):
        rc = RoleConfig(role=Role.DIRECTOR, blocked_tools={"read"})
        assert rc.can_use_tool("read") is False
        assert rc.can_use_tool("write") is True

    def test_default_limits(self):
        rc = RoleConfig(role=Role.DIRECTOR)
        assert rc.max_files_per_action == 3
        assert rc.max_lines_per_action == 500


class TestCapabilityChecker:
    def test_check_read_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_read(["a.py"])
        assert result.allowed is True

    def test_check_read_denied(self):
        rc = RoleConfig(role=Role.SYSTEM, capabilities={Capability.WRITE_FILES})
        checker = CapabilityChecker(rc)
        result = checker.check_read(["a.py"])
        assert result.allowed is False

    def test_check_write_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_write(["a.py"])
        assert result.allowed is True

    def test_check_write_too_many_files(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_write(["a.py"] * 10)
        assert result.allowed is False
        assert "Too many files" in result.reason

    def test_check_delete_without_capability(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_delete(["a.py"])
        assert result.allowed is False

    def test_check_delete_with_policy(self):
        rc = get_role_config(Role.DIRECTOR, policy={"write_tools": {"allow_delete": True}})
        checker = CapabilityChecker(rc, policy={"write_tools": {"allow_delete": True}})
        result = checker.check_delete(["a.py"])
        assert result.allowed is True

    def test_check_tool_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_tool("read")
        assert result.allowed is True

    def test_check_tool_blocked(self):
        rc = RoleConfig(
            role=Role.DIRECTOR,
            capabilities=DEFAULT_ROLE_CAPABILITIES[Role.DIRECTOR],
            blocked_tools={"read"},
        )
        checker = CapabilityChecker(rc)
        result = checker.check_tool("read")
        assert result.allowed is False

    def test_check_command_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_command("ls")
        assert result.allowed is True

    def test_check_test_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.QA))
        result = checker.check_test("pytest")
        assert result.allowed is True

    def test_check_patch_allowed(self):
        checker = CapabilityChecker(get_role_config(Role.DIRECTOR))
        result = checker.check_patch(["a.py"])
        assert result.allowed is True


class TestGetRoleConfig:
    def test_default(self):
        rc = get_role_config(Role.DIRECTOR)
        assert Capability.READ_FILES in rc.capabilities

    def test_with_policy(self):
        rc = get_role_config(Role.DIRECTOR, policy={"write_tools": {"allow_delete": True}})
        assert Capability.DELETE_FILES in rc.capabilities


class TestCheckActionAllowed:
    def test_read_action(self):
        result = check_action_allowed(Role.DIRECTOR, "read", ["a.py"])
        assert result.allowed is True

    def test_tool_action(self):
        result = check_action_allowed(Role.DIRECTOR, "tool", ["read"])
        assert result.allowed is True

    def test_command_action(self):
        result = check_action_allowed(Role.DIRECTOR, "command", ["ls"])
        assert result.allowed is True

    def test_test_action(self):
        result = check_action_allowed(Role.QA, "test", ["pytest"])
        assert result.allowed is True

    def test_unknown_action(self):
        result = check_action_allowed(Role.DIRECTOR, "unknown", ["a.py"])
        assert result.allowed is True


class TestValidateDirectorAction:
    def test_validate_read(self):
        result = validate_director_action("read", ["a.py"])
        assert result.allowed is True


class TestSkill:
    def test_creation(self):
        skill = Skill(id="s1", name="test_skill", description="desc")
        assert skill.id == "s1"
        assert skill.prompt_fragments == {}


class TestGetRoleCapabilities:
    def test_pm_workflow(self):
        result = get_role_capabilities("pm", "workflow")
        assert "workflow" in result
        assert "write_files" in result["workflow"]

    def test_unknown_role_fallback(self):
        result = get_role_capabilities("unknown", "default")
        assert "default" in result
        assert result["default"] == []


# ---------------------------------------------------------------------------
# Export Artifacts
# ---------------------------------------------------------------------------


class TestPMTaskDraft:
    def test_creation(self):
        draft = create_pm_task_draft("title", "desc", priority="high")
        assert draft.title == "title"
        assert draft.priority == "high"
        assert draft.dependencies == []

    def test_defaults(self):
        draft = PMTaskDraft(title="t", description="d")
        assert draft.priority == "medium"
        assert draft.estimated_hours is None


class TestPlanNotes:
    def test_creation(self):
        notes = create_plan_notes("summary", goals=["g1"])
        assert notes.summary == "summary"
        assert notes.goals == ["g1"]


class TestExecutionNotes:
    def test_creation(self):
        notes = create_execution_notes(changes_made=["fix bug"])
        assert notes.changes_made == ["fix bug"]
        assert notes.commands_executed == []


class TestPatchSummary:
    def test_creation(self):
        ps = create_patch_summary("desc", files_changed=["a.py"], lines_added=10)
        assert ps.description == "desc"
        assert ps.lines_added == 10


class TestQAAuditDraft:
    def test_creation(self):
        qa = create_qa_audit_draft("target", issues_found=[{"description": "bug"}])
        assert qa.target == "target"
        assert len(qa.issues_found) == 1


class TestRoleSessionExport:
    def test_creation(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="electron_workbench",
            workspace="/tmp/ws",
            created_at="2026-01-01T00:00:00Z",
        )
        assert rse.session_id == "s1"
        assert rse.role == "pm"

    def test_to_dict_skips_none(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="electron_workbench",
            workspace="/tmp/ws",
            created_at="2026-01-01T00:00:00Z",
        )
        d = rse.to_dict()
        assert "pm_task_draft" not in d
        assert "session_id" in d

    def test_to_dict_with_drafts(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="electron_workbench",
            workspace="/tmp/ws",
            created_at="2026-01-01T00:00:00Z",
            pm_task_draft=PMTaskDraft(title="t", description="d"),
        )
        d = rse.to_dict()
        assert "pm_task_draft" in d

    def test_to_markdown(self):
        rse = RoleSessionExport(
            session_id="s1",
            role="pm",
            host_kind="electron_workbench",
            workspace="/tmp/ws",
            created_at="2026-01-01T00:00:00Z",
            pm_task_draft=PMTaskDraft(title="t", description="d"),
        )
        md = rse.to_markdown()
        assert "Session Export" in md
        assert "PM Task Draft" in md


class TestExportType:
    def test_values(self):
        assert ExportType.PM_TASK_DRAFT.value == "pm_task_draft"
        assert ExportType.QA_AUDIT_DRAFT.value == "qa_audit_draft"


class TestExportFormat:
    def test_values(self):
        assert ExportFormat.JSON.value == "json"
        assert ExportFormat.MARKDOWN.value == "markdown"


class TestParseExportType:
    def test_valid(self):
        assert parse_export_type("pm_task_draft") == ExportType.PM_TASK_DRAFT

    def test_invalid_fallback(self):
        assert parse_export_type("invalid") == ExportType.PM_TASK_DRAFT


# ---------------------------------------------------------------------------
# Workflow
# ---------------------------------------------------------------------------


class TestPMWorkflowResult:
    def test_creation(self):
        result = PMWorkflowResult(run_id="r1", tasks=[], director_status="idle", qa_status="idle")
        assert result.run_id == "r1"
        assert result.metadata == {}


class TestDirectorWorkflowResult:
    def test_creation(self):
        result = DirectorWorkflowResult(run_id="r1", status="completed", completed_tasks=5, failed_tasks=0)
        assert result.status == "completed"
        assert result.completed_tasks == 5


class TestExecutionMode:
    def test_values(self):
        assert ExecutionMode.SEQUENTIAL.value == "sequential"
        assert ExecutionMode.PARALLEL.value == "parallel"


class TestCoercePositiveInt:
    def test_valid(self):
        assert _coerce_positive_int(5, 1) == 5

    def test_none(self):
        assert _coerce_positive_int(None, 3) == 3

    def test_negative(self):
        assert _coerce_positive_int(-5, 3) == 1

    def test_invalid(self):
        assert _coerce_positive_int("abc", 3) == 3


class TestCoerceExecutionMode:
    def test_valid_parallel(self):
        assert _coerce_execution_mode("parallel") == "parallel"

    def test_valid_sequential(self):
        assert _coerce_execution_mode("sequential") == "sequential"

    def test_invalid(self):
        assert _coerce_execution_mode("invalid") == "parallel"

    def test_custom_default(self):
        assert _coerce_execution_mode("invalid", "sequential") == "sequential"


# ---------------------------------------------------------------------------
# Defect
# ---------------------------------------------------------------------------


class TestDefectConstants:
    def test_default_fields(self):
        assert "defect_id" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "severity" in DEFAULT_DEFECT_TICKET_FIELDS
        assert "repro_steps" in DEFAULT_DEFECT_TICKET_FIELDS
        assert len(DEFAULT_DEFECT_TICKET_FIELDS) == 7
