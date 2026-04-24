"""Tests for polaris.delivery.http.schemas.

Covers Pydantic model instantiation, serialization, defaults, and edge cases.
"""

from __future__ import annotations

import pytest
from polaris.delivery.http.schemas import (
    AgentsApplyPayload,
    AgentsFeedbackPayload,
    AuditCleanupParams,
    AuditCleanupResponse,
    AuditEventResponse,
    AuditExportParams,
    AuditLogsResponse,
    AuditStatsResponse,
    AuditTraceResponse,
    AuditTriageRequest,
    AuditTriageResponse,
    AuditVerifyResponse,
    CodeRegionRequest,
    CodeRegionResponse,
    CorruptionRecord,
    DocsInitApplyPayload,
    DocsInitDialoguePayload,
    DocsInitDialogueTurn,
    DocsInitFile,
    DocsInitPreviewPayload,
    DocsInitSuggestPayload,
    FailureAnalysisRequest,
    FailureAnalysisResponse,
    FailureHopsResponse,
    ProjectScanRequest,
    ProjectScanResponse,
)
from pydantic import ValidationError


class TestDocsInitSchemas:
    """Tests for document initialization schemas."""

    def test_docs_init_preview_defaults(self) -> None:
        payload = DocsInitPreviewPayload()
        assert payload.mode == "minimal"
        assert payload.goal == ""
        assert payload.in_scope == ""
        assert payload.out_of_scope == ""
        assert payload.constraints == ""
        assert payload.definition_of_done == ""
        assert payload.backlog == ""

    def test_docs_init_preview_custom_values(self) -> None:
        payload = DocsInitPreviewPayload(mode="full", goal="test goal")
        assert payload.mode == "full"
        assert payload.goal == "test goal"

    def test_docs_init_suggest_defaults(self) -> None:
        payload = DocsInitSuggestPayload()
        assert payload.goal == ""

    def test_docs_init_dialogue_turn_defaults(self) -> None:
        turn = DocsInitDialogueTurn()
        assert turn.role == ""
        assert turn.content == ""
        assert turn.questions == []

    def test_docs_init_dialogue_turn_with_questions(self) -> None:
        turn = DocsInitDialogueTurn(role="user", content="hello", questions=["q1", "q2"])
        assert turn.questions == ["q1", "q2"]

    def test_docs_init_dialogue_payload_defaults(self) -> None:
        payload = DocsInitDialoguePayload()
        assert payload.message == ""
        assert payload.history == []

    def test_docs_init_dialogue_payload_with_history(self) -> None:
        turn = DocsInitDialogueTurn(role="user", content="hello")
        payload = DocsInitDialoguePayload(message="hi", history=[turn])
        assert len(payload.history) == 1
        assert payload.history[0].role == "user"

    def test_docs_init_file_required_fields(self) -> None:
        f = DocsInitFile(path="docs/readme.md", content="# Hello")
        assert f.path == "docs/readme.md"
        assert f.content == "# Hello"

    def test_docs_init_file_missing_path(self) -> None:
        with pytest.raises(ValidationError):
            DocsInitFile()  # type: ignore[call-arg]

    def test_docs_init_apply_payload_defaults(self) -> None:
        payload = DocsInitApplyPayload()
        assert payload.mode == "minimal"
        assert payload.target_root == "docs"
        assert payload.files == []

    def test_docs_init_apply_payload_with_files(self) -> None:
        f = DocsInitFile(path="a.md", content="A")
        payload = DocsInitApplyPayload(files=[f])
        assert len(payload.files) == 1


class TestAgentsSchemas:
    """Tests for agent-related schemas."""

    def test_agents_apply_payload_defaults(self) -> None:
        payload = AgentsApplyPayload()
        assert payload.draft_path is None

    def test_agents_apply_payload_with_path(self) -> None:
        payload = AgentsApplyPayload(draft_path="/tmp/draft.md")
        assert payload.draft_path == "/tmp/draft.md"

    def test_agents_feedback_payload_defaults(self) -> None:
        payload = AgentsFeedbackPayload()
        assert payload.text == ""

    def test_agents_feedback_payload_custom(self) -> None:
        payload = AgentsFeedbackPayload(text="looks good")
        assert payload.text == "looks good"


class TestAuditEventResponse:
    """Tests for AuditEventResponse schema."""

    def test_required_fields(self) -> None:
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="abc",
            signature="sig",
        )
        assert event.event_id == "evt-1"
        assert event.version == "1.0"
        assert event.source == {}
        assert event.task == {}

    def test_optional_defaults(self) -> None:
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="abc",
            signature="sig",
        )
        assert event.data == {}
        assert event.context == {}

    def test_custom_optional_fields(self) -> None:
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            version="2.0",
            source={"app": "test"},
            prev_hash="abc",
            signature="sig",
        )
        assert event.version == "2.0"
        assert event.source == {"app": "test"}


class TestAuditLogsResponse:
    """Tests for AuditLogsResponse schema."""

    def test_basic_instantiation(self) -> None:
        resp = AuditLogsResponse(events=[{"id": "1"}], pagination={"page": 1})
        assert resp.events == [{"id": "1"}]
        assert resp.pagination == {"page": 1}


class TestAuditExportParams:
    """Tests for AuditExportParams schema."""

    def test_defaults(self) -> None:
        params = AuditExportParams()
        assert params.format == "json"
        assert params.start_time is None
        assert params.end_time is None
        assert params.event_types is None
        assert params.include_data is True

    def test_csv_format(self) -> None:
        params = AuditExportParams(format="csv")
        assert params.format == "csv"


class TestAuditVerifyResponse:
    """Tests for AuditVerifyResponse schema."""

    def test_basic_instantiation(self) -> None:
        resp = AuditVerifyResponse(
            chain_valid=True,
            first_event_hash="abc",
            last_event_hash="def",
            total_events=10,
            gap_count=0,
            verified_at="2024-01-01T00:00:00Z",
        )
        assert resp.chain_valid is True
        assert resp.total_events == 10
        assert resp.invalid_events == []

    def test_with_invalid_events(self) -> None:
        resp = AuditVerifyResponse(
            chain_valid=False,
            first_event_hash="abc",
            last_event_hash="def",
            total_events=10,
            gap_count=2,
            verified_at="2024-01-01T00:00:00Z",
            invalid_events=[{"event_id": "bad", "reason": "hash mismatch"}],
        )
        assert len(resp.invalid_events) == 1


class TestAuditStatsResponse:
    """Tests for AuditStatsResponse schema."""

    def test_basic_instantiation(self) -> None:
        resp = AuditStatsResponse(
            stats={"total": 100},
            time_range={"start": "2024-01-01", "end": "2024-01-31"},
        )
        assert resp.stats["total"] == 100


class TestAuditCleanupParams:
    """Tests for AuditCleanupParams schema."""

    def test_defaults(self) -> None:
        params = AuditCleanupParams()
        assert params.dry_run is True
        assert params.older_than_days is None

    def test_non_dry_run(self) -> None:
        params = AuditCleanupParams(dry_run=False, older_than_days=30)
        assert params.dry_run is False
        assert params.older_than_days == 30


class TestAuditCleanupResponse:
    """Tests for AuditCleanupResponse schema."""

    def test_basic_instantiation(self) -> None:
        resp = AuditCleanupResponse(
            would_delete=10,
            would_free_mb=50.5,
            dry_run=True,
            cutoff_date="2024-01-01",
        )
        assert resp.would_delete == 10
        assert resp.affected_files == []


class TestAuditTriageRequest:
    """Tests for AuditTriageRequest schema."""

    def test_defaults(self) -> None:
        req = AuditTriageRequest()
        assert req.run_id is None
        assert req.task_id is None
        assert req.trace_id is None

    def test_with_values(self) -> None:
        req = AuditTriageRequest(run_id="r1", task_id="t1")
        assert req.run_id == "r1"


class TestAuditTriageResponse:
    """Tests for AuditTriageResponse schema."""

    def test_required_and_defaults(self) -> None:
        resp = AuditTriageResponse(status="success", generated_at="2024-01-01T00:00:00Z")
        assert resp.status == "success"
        assert resp.pm_quality_history == []
        assert resp.leakage_findings == []
        assert resp.next_risks == []

    def test_with_nested_data(self) -> None:
        resp = AuditTriageResponse(
            status="partial",
            run_id="r1",
            pm_quality_history=[{"score": 0.9}],
            generated_at="2024-01-01T00:00:00Z",
        )
        assert resp.pm_quality_history == [{"score": 0.9}]


class TestFailureHopsResponse:
    """Tests for FailureHopsResponse schema."""

    def test_required_fields(self) -> None:
        resp = FailureHopsResponse(
            run_id="r1",
            generated_at="2024-01-01T00:00:00Z",
            ready=True,
            has_failure=False,
            failure_code="",
        )
        assert resp.schema_version == 2
        assert resp.failure_event_seq is None

    def test_with_hops(self) -> None:
        resp = FailureHopsResponse(
            run_id="r1",
            generated_at="2024-01-01T00:00:00Z",
            ready=True,
            has_failure=True,
            failure_code="ERR_1",
            failure_event_seq=5,
            hop1_phase={"name": "execution"},
        )
        assert resp.hop1_phase == {"name": "execution"}


class TestFailureAnalysisRequest:
    """Tests for FailureAnalysisRequest schema."""

    def test_defaults(self) -> None:
        req = FailureAnalysisRequest()
        assert req.run_id is None
        assert req.task_id is None
        assert req.error_message is None
        assert req.time_range == "1h"
        assert req.depth == 3

    def test_depth_validation(self) -> None:
        with pytest.raises(ValidationError):
            FailureAnalysisRequest(depth=0)
        with pytest.raises(ValidationError):
            FailureAnalysisRequest(depth=4)

    def test_valid_depths(self) -> None:
        for d in (1, 2, 3):
            req = FailureAnalysisRequest(depth=d)
            assert req.depth == d


class TestFailureAnalysisResponse:
    """Tests for FailureAnalysisResponse schema."""

    def test_required_fields(self) -> None:
        resp = FailureAnalysisResponse(
            depth=3,
            recommended_action="retry",
            failure_detected=False,
            event_count=0,
        )
        assert resp.depth == 3
        assert resp.failure_hops == []
        assert resp.timeline == []


class TestProjectScanRequest:
    """Tests for ProjectScanRequest schema."""

    def test_defaults(self) -> None:
        req = ProjectScanRequest()
        assert req.scope == "full"
        assert req.focus is None
        assert req.max_files == 800
        assert req.max_findings == 300

    def test_max_files_validation(self) -> None:
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_files=0)
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_files=5001)

    def test_max_findings_validation(self) -> None:
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_findings=0)
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_findings=2001)


class TestProjectScanResponse:
    """Tests for ProjectScanResponse schema."""

    def test_defaults(self) -> None:
        resp = ProjectScanResponse(scope="full")
        assert resp.focus == ""
        assert resp.summary == {}
        assert resp.findings == []
        assert resp.recommendations == []


class TestCodeRegionRequest:
    """Tests for CodeRegionRequest schema."""

    def test_defaults(self) -> None:
        req = CodeRegionRequest()
        assert req.file_path is None
        assert req.function_name is None
        assert req.lines is None

    def test_with_values(self) -> None:
        req = CodeRegionRequest(file_path="src/main.py", lines="10-50")
        assert req.file_path == "src/main.py"
        assert req.lines == "10-50"


class TestCodeRegionResponse:
    """Tests for CodeRegionResponse schema."""

    def test_required_field(self) -> None:
        resp = CodeRegionResponse(file="src/main.py")
        assert resp.file == "src/main.py"
        assert resp.function_name == ""
        assert resp.line_range == {}
        assert resp.findings == []


class TestAuditTraceResponse:
    """Tests for AuditTraceResponse schema."""

    def test_required_field(self) -> None:
        resp = AuditTraceResponse(trace_id="t1", event_count=5)
        assert resp.trace_id == "t1"
        assert resp.event_count == 5
        assert resp.run_ids == []
        assert resp.task_ids == []
        assert resp.first_timestamp == ""
        assert resp.last_timestamp == ""
        assert resp.timeline == []


class TestCorruptionRecord:
    """Tests for CorruptionRecord schema."""

    def test_required_fields(self) -> None:
        record = CorruptionRecord(
            timestamp="2024-01-01T00:00:00Z",
            file_path="/tmp/log",
            offset=1024,
            error_type="checksum",
            error_message="bad checksum",
            line_preview="...",
        )
        assert record.recovered is False

    def test_custom_recovered(self) -> None:
        record = CorruptionRecord(
            timestamp="2024-01-01T00:00:00Z",
            file_path="/tmp/log",
            offset=1024,
            error_type="checksum",
            error_message="bad checksum",
            line_preview="...",
            recovered=True,
        )
        assert record.recovered is True


class TestSerialization:
    """Tests for model serialization and deserialization."""

    def test_docs_init_preview_serialization(self) -> None:
        payload = DocsInitPreviewPayload(mode="full", goal="test")
        data = payload.model_dump()
        assert data["mode"] == "full"
        assert data["goal"] == "test"

    def test_docs_init_preview_deserialization(self) -> None:
        raw = {
            "mode": "full",
            "goal": "test",
            "in_scope": "",
            "out_of_scope": "",
            "constraints": "",
            "definition_of_done": "",
            "backlog": "",
        }
        payload = DocsInitPreviewPayload(**raw)
        assert payload.mode == "full"

    def test_audit_event_json_roundtrip(self) -> None:
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="abc",
            signature="sig",
            data={"key": "value"},
        )
        json_str = event.model_dump_json()
        assert "evt-1" in json_str
        assert "key" in json_str
