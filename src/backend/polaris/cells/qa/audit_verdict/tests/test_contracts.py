"""Tests for qa.audit_verdict public contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from polaris.cells.audit.evidence.public.contracts import (
    AppendEvidenceEventCommandV1,
    EvidenceAppendedEventV1,
)
from polaris.cells.qa.audit_verdict.public.contracts import (
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
)
from polaris.cells.qa.audit_verdict.public.service import (
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
    assert result.ok is True
    assert result.task_id == "qa-task-1"
    assert result.workspace == str(workspace)
    assert result.verdict == "PASS"
    assert result.score == 1.0
    assert result.findings == ()
    envelope_payload = result.metadata["qa_verdict_envelope"]
    assert envelope_payload["schema_version"] == "qa.verdict_envelope.v1"
    assert envelope_payload["classification"]["failure_class"] == "PASSED"
    assert result.metadata["responsible_layer"] == "qa"

    envelope = get_qa_verdict_envelope(
        GetQaVerdictQueryV1(task_id="qa-task-1", workspace=str(workspace), run_id="run-1")
    )
    assert isinstance(envelope, QaVerdictEnvelopeV1)
    assert envelope.task_id == "qa-task-1"
    assert envelope.classification.failure_class == "PASSED"


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
    assert result.verdict == "FAIL"
    assert result.score == 0.0
    assert any("Director changed_files evidence is required" in finding for finding in result.findings)
    assert result.metadata["failure_class"] == "IMPLEMENTATION_DEFECT"
    assert result.metadata["responsible_layer"] == "director"


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
