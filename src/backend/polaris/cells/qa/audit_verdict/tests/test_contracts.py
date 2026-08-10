"""Tests for qa.audit_verdict public contracts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.audit.evidence.public.contracts import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
)
from polaris.cells.control_plane.run_ledger.public import FailureClassV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.qa.audit_verdict.public.contracts import (
    CommitQaRoleVerdictCommandV1,
    FailureSignalV1,
    GetQaVerdictQueryV1,
    ParseTracebackFramesCommandV1,
    ParseTracebackFramesResultV1,
    QaAuditError,
    QaAuditResultV1,
    QaVerdictEnvelopeV1,
    QaVerdictIssuedEventV1,
    RunQaAuditCommandV1,
    RunVisualQaAuditCommandV1,
    TracebackFrameV1,
    VisualAuditFindingV1,
    VisualQaAuditResultV1,
    build_qa_failure_classification_v1,
    build_qa_pass_classification_v1,
    normalize_qa_failure_class,
    project_qa_failure_execution_state,
)
from polaris.cells.qa.audit_verdict.public.service import (
    commit_qa_role_verdict,
    get_qa_verdict_envelope,
    parse_traceback_frames,
    run_qa_audit,
    run_visual_qa_audit,
)


class TestRunQaAuditCommandV1:
    """RunQaAuditCommandV1 validation and normalisation."""

    def test_required_fields(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp")
        assert cmd.task_id == "t1"
        assert cmd.workspace == "/tmp"
        assert cmd.run_id is None
        assert cmd.criteria == {}
        assert cmd.evidence_paths == ()

    def test_strips_whitespace(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="  t2  ", workspace="  /ws  ")
        assert cmd.task_id == "t2"
        assert cmd.workspace == "/ws"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RunQaAuditCommandV1(task_id="", workspace="/tmp")  # type: ignore[arg-type]

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RunQaAuditCommandV1(task_id="t1", workspace="   ")  # type: ignore[arg-type]

    def test_criteria_normalised_to_dict(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp", criteria={"k": "v"})
        assert isinstance(cmd.criteria, dict)
        assert cmd.criteria["k"] == "v"

    def test_evidence_paths_filtered_and_tuple(self) -> None:
        cmd = RunQaAuditCommandV1(
            task_id="t1",
            workspace="/tmp",
            evidence_paths=["a.py", "  ", "b.py"],  # type: ignore[arg-type]
        )
        assert cmd.evidence_paths == ("a.py", "b.py")

    def test_run_id_optional(self) -> None:
        cmd = RunQaAuditCommandV1(task_id="t1", workspace="/tmp", run_id="r1")
        assert cmd.run_id == "r1"


class TestCommitQaRoleVerdictCommandV1:
    def test_pass_report_is_normalized(self) -> None:
        command = CommitQaRoleVerdictCommandV1(
            task_id=" TASK-3 ",
            workspace=" /workspace ",
            run_id=" director-3 ",
            verdict=" pass ",
            passed=True,
            score=96,
            target_files=("src/main.ts", "src/main.ts"),
        )

        assert command.task_id == "TASK-3"
        assert command.run_id == "director-3"
        assert command.verdict == "PASS"
        assert command.target_files == ("src/main.ts",)

    def test_rejects_inconsistent_pass_flag(self) -> None:
        with pytest.raises(ValueError, match="passed must be true exactly"):
            CommitQaRoleVerdictCommandV1(
                task_id="TASK-3",
                workspace="/workspace",
                run_id="director-3",
                verdict="FAIL",
                passed=True,
            )


class TestGetQaVerdictQueryV1:
    """GetQaVerdictQueryV1 validation."""

    def test_required_fields(self) -> None:
        q = GetQaVerdictQueryV1(task_id="t1", workspace="/tmp")
        assert q.task_id == "t1"
        assert q.workspace == "/tmp"

    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GetQaVerdictQueryV1(task_id="", workspace="/tmp")  # type: ignore[arg-type]

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GetQaVerdictQueryV1(task_id="t1", workspace="")  # type: ignore[arg-type]


class TestQaFailureClassificationBuilder:
    """Shared QA failure classification builder."""

    def test_failure_class_values_are_owned_by_run_ledger(self) -> None:
        assert FailureClassV1.IMPLEMENTATION_DEFECT.value == "IMPLEMENTATION_DEFECT"
        assert FailureClassV1.IMPLEMENTATION_DEFECT_BOUNCE_LIMIT.value == "IMPLEMENTATION_DEFECT_BOUNCE_LIMIT"
        assert FailureClassV1.DEFERRED_FOLLOWUP_REQUIRED.value == "DEFERRED_FOLLOWUP_REQUIRED"
        assert FailureClassV1.BLUEPRINT_VERIFY_INVALID.value == "BLUEPRINT_VERIFY_INVALID"
        assert FailureClassV1.TEST_ENVIRONMENT_FAILURE.value == "TEST_ENVIRONMENT_FAILURE"
        assert "PASSED" not in FailureClassV1.__members__

    def test_builds_canonical_classification(self) -> None:
        classification = build_qa_failure_classification_v1(
            failure_class="scope_mismatch",
            route="pending_design",
            reason="scope mismatch",
            repairable_by_director=False,
            requires_ce_replan=True,
            evidence_refs=["qa/latest.json", "qa/latest.json", ""],
        )

        assert classification.failure_class == "BLUEPRINT_SCOPE_MISMATCH"
        assert classification.route == "pending_design"
        assert classification.requires_ce_replan is True
        assert classification.evidence_refs == ("qa/latest.json",)

    def test_aliases_normalize_to_canonical_classification(self) -> None:
        classification = build_qa_failure_classification_v1(
            failure_class="TOOL_DISPATCH_DROPPED",
            route="hard_stop",
            reason="tool calls dropped",
            repairable_by_director=False,
            severity="critical",
        )

        assert classification.failure_class == "TOOL_DISPATCH_DROPPED"
        assert classification.severity == "critical"

    def test_pass_classification_has_no_failure_class(self) -> None:
        classification = build_qa_pass_classification_v1(reason="QA evidence accepted")

        assert classification.failure_class is None
        assert classification.route == "resolved"
        assert classification.to_dict()["failure_class"] is None

    def test_failure_builder_rejects_passed_verdict_state(self) -> None:
        with pytest.raises(ValueError, match="verdict state"):
            build_qa_failure_classification_v1(
                failure_class="PASSED",
                route="resolved",
                reason="QA evidence accepted",
                repairable_by_director=False,
            )

    def test_overlapping_platform_failures_use_run_ledger_normalization(self) -> None:
        assert normalize_qa_failure_class("tool dispatch dropped") == FailureClassV1.TOOL_DISPATCH_DROPPED.value
        assert normalize_qa_failure_class("missing-effect-receipt") == FailureClassV1.MISSING_EFFECT_RECEIPT.value
        assert normalize_qa_failure_class("incomplete-materialization") == "INCOMPLETE_MATERIALIZATION"
        assert normalize_qa_failure_class("missing entrypoint target") == "MISSING_ENTRYPOINT_TARGET"

    def test_projects_failure_class_to_runtime_execution_state(self) -> None:
        assert project_qa_failure_execution_state("incomplete_materialization") == "FAILED_ARTIFACT"
        assert project_qa_failure_execution_state("missing_entrypoint_target") == "FAILED_ARTIFACT"
        assert project_qa_failure_execution_state("implementation_defect") == "FAILED_ARTIFACT"
        assert project_qa_failure_execution_state("tool_dispatch_dropped") == "FAILED_PLATFORM"
        assert project_qa_failure_execution_state("execution_evidence_missing") == "BLOCKED_WITH_REASON"
        assert project_qa_failure_execution_state("dependency_not_unlocked") == "BLOCKED_WITH_REASON"


class TestQaVerdictIssuedEventV1:
    """QaVerdictIssuedEventV1 validation."""

    def test_required_fields(self) -> None:
        evt = QaVerdictIssuedEventV1(
            event_id="e1",
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            issued_at="2026-01-01T00:00:00Z",
        )
        assert evt.verdict == "PASS"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaVerdictIssuedEventV1(
                event_id="",
                task_id="t1",
                workspace="/tmp",
                verdict="PASS",
                issued_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]

    def test_empty_verdict_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaVerdictIssuedEventV1(
                event_id="e1",
                task_id="t1",
                workspace="/tmp",
                verdict="  ",
                issued_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]


class TestQaAuditResultV1:
    """QaAuditResultV1 validation."""

    def test_pass_result(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            score=0.9,
        )
        assert r.ok is True
        assert r.score == 0.9
        assert r.findings == ()
        assert r.suggestions == ()

    def test_fail_result(self) -> None:
        r = QaAuditResultV1(
            ok=False,
            task_id="t1",
            workspace="/tmp",
            verdict="FAIL",
            findings=["f1", "f2"],  # type: ignore[arg-type]
        )
        assert r.ok is False
        assert r.findings == ("f1", "f2")

    def test_negative_score_raises(self) -> None:
        with pytest.raises(ValueError, match="score must be >= 0"):
            QaAuditResultV1(
                ok=True,
                task_id="t1",
                workspace="/tmp",
                verdict="PASS",
                score=-0.1,
            )

    def test_findings_normalised_to_tuple(self) -> None:
        r = QaAuditResultV1(
            ok=True,
            task_id="t1",
            workspace="/tmp",
            verdict="PASS",
            findings=["a", "b"],  # type: ignore[arg-type]
        )
        assert isinstance(r.findings, tuple)
        assert r.findings == ("a", "b")


class TestVisualQaAuditContracts:
    def test_visual_audit_command_requires_image_refs_and_model_capability_ref(self) -> None:
        cmd = RunVisualQaAuditCommandV1(
            task_id="qa-visual-1",
            workspace="/repo",
            image_refs=("audit.evidence:image:screenshot-1",),
            model_capability_ref="llm.control_plane:model-capability:qa:image_input:abc",
            criteria={"viewport": "desktop"},
            evidence_paths=("runtime/evidence/screenshot-1.png",),
        )

        assert cmd.image_refs == ("audit.evidence:image:screenshot-1",)
        assert cmd.model_capability_ref == "llm.control_plane:model-capability:qa:image_input:abc"
        assert cmd.criteria["viewport"] == "desktop"
        assert cmd.evidence_paths == ("runtime/evidence/screenshot-1.png",)

        with pytest.raises(ValueError, match="image_refs must include at least one image ref"):
            RunVisualQaAuditCommandV1(
                task_id="qa-visual-1",
                workspace="/repo",
                image_refs=(),
                model_capability_ref="llm.control_plane:model-capability:qa:image_input:abc",
            )

    def test_visual_audit_command_rejects_unverified_model_capability_ref(self) -> None:
        with pytest.raises(
            ValueError,
            match=r"model_capability_ref must point to llm\.control_plane image_input capability",
        ):
            RunVisualQaAuditCommandV1(
                task_id="qa-visual-1",
                workspace="/repo",
                image_refs=("audit.evidence:image:screenshot-1",),
                model_capability_ref="qa.audit_verdict:model-capability:qa:image_input:abc",
            )

    def test_visual_audit_result_carries_typed_findings(self) -> None:
        finding = VisualAuditFindingV1(
            finding_id="visual-finding-1",
            image_ref="audit.evidence:image:screenshot-1",
            category="layout_overlap",
            summary="Button text overlaps icon",
            severity="error",
            confidence=0.91,
        )
        result = VisualQaAuditResultV1(
            ok=False,
            task_id="qa-visual-1",
            workspace="/repo",
            verdict="FAIL",
            image_refs=("audit.evidence:image:screenshot-1",),
            model_capability_ref="llm.control_plane:model-capability:qa:image_input:abc",
            findings=(finding,),
            score=0.0,
        )

        assert result.findings == (finding,)
        assert result.image_refs == ("audit.evidence:image:screenshot-1",)
        assert result.model_capability_ref.endswith(":abc")


class TestTracebackFailureSignalContracts:
    def test_traceback_frame_requires_positive_line(self) -> None:
        frame = TracebackFrameV1(path="app.py", line=12, function="handle", code="return explode()")
        assert frame.path == "app.py"
        assert frame.line == 12

        with pytest.raises(ValueError, match="line must be >= 1"):
            TracebackFrameV1(path="app.py", line=0, function="handle")

    def test_failure_signal_carries_typed_frames(self) -> None:
        frame = TracebackFrameV1(path="app.py", line=12, function="handle")
        signal = FailureSignalV1(
            signal_id="sig-1",
            task_id="qa-task-1",
            workspace="/repo",
            signal_type="ValueError",
            summary="ValueError: boom",
            frames=(frame,),
        )
        assert signal.frames == (frame,)
        assert signal.severity == "error"

    def test_parse_traceback_command_requires_traceback_text(self) -> None:
        with pytest.raises(ValueError, match="traceback_text must be a non-empty string"):
            ParseTracebackFramesCommandV1(task_id="qa-task-1", workspace="/repo", traceback_text="")

    def test_parse_traceback_result_wraps_signal(self) -> None:
        signal = FailureSignalV1(
            signal_id="sig-1",
            task_id="qa-task-1",
            workspace="/repo",
            signal_type="RuntimeError",
            summary="RuntimeError: boom",
        )
        result = ParseTracebackFramesResultV1(ok=True, task_id="qa-task-1", workspace="/repo", signal=signal)
        assert result.signal.signal_id == "sig-1"
        assert result.frame_count == 0


class TestQaAuditError:
    """QaAuditError structured error."""

    def test_code_defaults_to_qa_audit_error(self) -> None:
        err = QaAuditError("something went wrong")
        assert err.code == "qa_audit_error"
        assert str(err) == "something went wrong"

    def test_custom_code_and_details(self) -> None:
        err = QaAuditError("boom", code="SECURITY_VIOLATION", details={"path": "/etc"})
        assert err.code == "SECURITY_VIOLATION"
        assert err.details["path"] == "/etc"

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaAuditError("")  # type: ignore[arg-type]

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QaAuditError("msg", code="")  # type: ignore[arg-type]


def test_run_qa_audit_public_service_executes_typed_command(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="qa-audit-public-service-test",
        )
    )
    (workspace / "service.py").write_text("def ok() -> str:\n    return 'ok'\n", encoding="utf-8")

    result = run_qa_audit(
        RunQaAuditCommandV1(
            task_id="qa-task-1",
            workspace=str(workspace),
            run_id="run-1",
            criteria={
                "task_subject": "Audit changed Python service",
                "changed_files": ("service.py",),
                "require_changed_files": True,
            },
            evidence_paths=("pytest-report.xml",),
        )
    )

    assert isinstance(result, QaAuditResultV1)
    assert result.ok is False
    assert result.task_id == "qa-task-1"
    assert result.workspace == str(workspace)
    assert result.verdict == "BLOCKED"
    assert result.score == 0.0
    assert result.findings == ()
    envelope_payload = result.metadata["qa_verdict_envelope"]
    assert envelope_payload["schema_version"] == "qa.verdict_envelope.v1"
    assert envelope_payload["next_stage"] == "pending_qa"
    assert "task_boundary_missing" in envelope_payload["evidence"]["conflict_matrix"]["conflicts"]
    assert envelope_payload["ledger"]["available"] is True
    assert result.metadata["responsible_layer"] == "execution_control_plane"
    assert result.metadata["audit_evidence"]["authoritative"] is False
    assert result.metadata["qa_verdict_committed"] is True
    final_receipt = result.metadata["qa_verdict_commit_receipt"]
    assert final_receipt["verdict"] == "BLOCKED"
    assert final_receipt["envelope_hash"] == envelope_payload["content_hash"]
    assert final_receipt["append_id"]
    assert final_receipt["event_hash"]

    envelope = get_qa_verdict_envelope(
        GetQaVerdictQueryV1(task_id="qa-task-1", workspace=str(workspace), run_id="run-1")
    )
    assert isinstance(envelope, QaVerdictEnvelopeV1)
    assert envelope.task_id == "qa-task-1"
    assert envelope.verdict == "BLOCKED"
    assert envelope.next_stage == "pending_qa"


def test_run_qa_audit_public_service_reports_missing_director_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_qa_audit(
        RunQaAuditCommandV1(
            task_id="qa-task-2",
            workspace=str(workspace),
            criteria={
                "task_subject": "Audit task without director changed_files evidence",
                "require_changed_files": True,
            },
        )
    )

    assert result.ok is False
    assert result.verdict == "BLOCKED"
    assert result.score == 0.0
    assert any("Director changed_files evidence is required" in finding for finding in result.findings)
    assert result.metadata["failure_class"] == "LEDGER_PROJECTION_INCOMPLETE"
    assert result.metadata["responsible_layer"] == "execution_control_plane"
    assert result.metadata["qa_verdict_committed"] is False
    assert result.metadata["qa_verdict_commit_receipt"] == {}


def test_commit_qa_role_verdict_uses_evidence_barrier_before_final_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.cells.qa.audit_verdict.public import service as service_module

    commits: list[tuple[str, dict[str, object]]] = []

    def commit_evidence(**kwargs: object) -> SimpleNamespace:
        commits.append(("evidence", dict(kwargs)))
        return SimpleNamespace(
            to_dict=lambda: {"run_id": "director-3", "append_id": "append-1", "event_hash": "event-1"}
        )

    envelope_payload = {
        "schema_version": "qa.verdict_envelope.v1",
        "workspace": str(tmp_path),
        "run_id": "director-3",
        "task_id": "TASK-3",
        "verdict": "PASS",
        "ok": True,
        "classification": {"failure_class": None, "responsible_layer": "qa"},
        "content_hash": "envelope-hash",
    }
    envelope = SimpleNamespace(
        ok=True,
        verdict="PASS",
        findings=(),
        content_hash="envelope-hash",
        to_dict=lambda: dict(envelope_payload),
    )

    def build_envelope(**kwargs: object) -> SimpleNamespace:
        commits.append(("envelope", dict(kwargs)))
        return envelope

    def commit_verdict(**kwargs: object) -> SimpleNamespace:
        commits.append(("verdict", dict(kwargs)))
        return SimpleNamespace(
            to_dict=lambda: {
                "run_id": "director-3",
                "append_id": "append-2",
                "event_hash": "event-2",
                "envelope_hash": "envelope-hash",
                "verdict": "PASS",
            }
        )

    monkeypatch.setattr(service_module, "commit_qa_evidence", commit_evidence)
    monkeypatch.setattr(service_module, "_build_qa_verdict_envelope", build_envelope)
    monkeypatch.setattr(service_module, "commit_qa_verdict", commit_verdict)

    result = commit_qa_role_verdict(
        CommitQaRoleVerdictCommandV1(
            task_id="TASK-3",
            workspace=str(tmp_path),
            run_id="director-3",
            verdict="PASS",
            passed=True,
            score=96,
            target_files=("src/main.ts",),
            report_ref="runtime/qa/report.json",
            report_content_hash="report-hash",
            job_token={"run_id": "director-3", "capability_audit": {"ok": True}},
        )
    )

    assert [name for name, _ in commits] == ["evidence", "envelope", "verdict"]
    assert commits[2][1]["evidence_commit_receipt"] == {
        "run_id": "director-3",
        "append_id": "append-1",
        "event_hash": "event-1",
    }
    assert result.ok is True
    assert result.verdict == "PASS"
    assert result.metadata["qa_verdict_committed"] is True


def test_run_visual_qa_audit_public_service_records_image_evidence_refs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = run_visual_qa_audit(
        RunVisualQaAuditCommandV1(
            task_id="qa-visual-1",
            workspace=str(workspace),
            image_refs=("audit.evidence:image:screenshot-1",),
            model_capability_ref="llm.control_plane:model-capability:qa:image_input:abc",
            criteria={"assertions": ("no visual overlap",)},
            evidence_paths=("runtime/evidence/screenshot-1.png",),
        )
    )

    assert isinstance(result, VisualQaAuditResultV1)
    assert result.ok is True
    assert result.verdict == "VISUAL_AUDIT_RECORDED"
    assert result.image_refs == ("audit.evidence:image:screenshot-1",)
    assert result.evidence_refs == (
        "runtime/evidence/screenshot-1.png",
        "runtime/evidence/qa.visual_audit.jsonl",
    )


class _FakeEvidenceAppendService:
    def __init__(self) -> None:
        self.commands: list[AppendEvidenceEventCommandV1] = []

    def append_evidence_event(self, command: AppendEvidenceEventCommandV1) -> EvidenceAppendedEventV1:
        self.commands.append(command)
        return EvidenceAppendedEventV1(kind=command.kind, receipt_path="runtime/evidence/qa.visual_audit.jsonl")


def test_run_visual_qa_audit_public_service_appends_truthlog_evidence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    evidence_service = _FakeEvidenceAppendService()

    result = run_visual_qa_audit(
        RunVisualQaAuditCommandV1(
            task_id="qa-visual-2",
            workspace=str(workspace),
            run_id="run-visual-1",
            image_refs=("audit.evidence:image:screenshot-2",),
            model_capability_ref="llm.control_plane:model-capability:qa:image_input:def",
            criteria={"assertions": ("no clipped text",)},
            evidence_paths=("runtime/evidence/screenshot-2.png",),
        ),
        evidence_service=evidence_service,
    )

    assert result.evidence_refs == (
        "runtime/evidence/screenshot-2.png",
        "runtime/evidence/qa.visual_audit.jsonl",
    )
    assert len(evidence_service.commands) == 1
    command = evidence_service.commands[0]
    assert command.kind == "qa.visual_audit"
    assert command.workspace == str(workspace)
    assert command.payload["task_id"] == "qa-visual-2"
    assert command.payload["run_id"] == "run-visual-1"
    assert command.payload["image_refs"] == ("audit.evidence:image:screenshot-2",)
    assert command.payload["model_capability_ref"] == "llm.control_plane:model-capability:qa:image_input:def"


def test_parse_traceback_frames_public_service_returns_typed_failure_signal() -> None:
    traceback_text = """Traceback (most recent call last):
  File "/repo/app.py", line 10, in handle
    return explode()
  File "/repo/app.py", line 6, in explode
    raise ValueError("boom")
ValueError: boom
"""

    result = parse_traceback_frames(
        ParseTracebackFramesCommandV1(
            task_id="qa-task-3",
            workspace="/repo",
            run_id="run-1",
            traceback_text=traceback_text,
            metadata={"source": "pytest"},
        )
    )

    assert isinstance(result, ParseTracebackFramesResultV1)
    assert result.ok is True
    assert result.task_id == "qa-task-3"
    assert result.signal.signal_type == "ValueError"
    assert result.signal.summary == "ValueError: boom"
    assert result.frame_count == 2
    assert result.signal.frames[0].path == "/repo/app.py"
    assert result.signal.frames[0].line == 10
    assert result.signal.frames[0].function == "handle"
    assert result.signal.frames[0].code == "return explode()"
