"""Tests for polaris.delivery.http.schemas module."""

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


class TestAgentsApplyPayload:
    """Tests for AgentsApplyPayload schema."""

    def test_default_draft_path_none(self) -> None:
        """Test default draft_path is None."""
        payload = AgentsApplyPayload()
        assert payload.draft_path is None

    def test_explicit_draft_path(self) -> None:
        """Test explicit draft_path."""
        payload = AgentsApplyPayload(draft_path="/path/to/draft")
        assert payload.draft_path == "/path/to/draft"

    def test_empty_string_draft_path(self) -> None:
        """Test empty string draft_path is preserved."""
        payload = AgentsApplyPayload(draft_path="")
        assert payload.draft_path == ""


class TestAgentsFeedbackPayload:
    """Tests for AgentsFeedbackPayload schema."""

    def test_default_text_empty(self) -> None:
        """Test default text is empty string."""
        payload = AgentsFeedbackPayload()
        assert payload.text == ""

    def test_explicit_text(self) -> None:
        """Test explicit text."""
        payload = AgentsFeedbackPayload(text="feedback")
        assert payload.text == "feedback"


class TestDocsInitPayloads:
    """Tests for DocsInit related schemas."""

    def test_preview_default_mode(self) -> None:
        """Test default mode for preview payload."""
        payload = DocsInitPreviewPayload()
        assert payload.mode == "minimal"

    def test_preview_custom_mode(self) -> None:
        """Test custom mode for preview payload."""
        payload = DocsInitPreviewPayload(mode="full")
        assert payload.mode == "full"

    def test_suggest_default_fields(self) -> None:
        """Test default fields for suggest payload."""
        payload = DocsInitSuggestPayload()
        assert payload.goal == ""
        assert payload.in_scope == ""

    def test_dialogue_turn_default_questions(self) -> None:
        """Test default questions list."""
        turn = DocsInitDialogueTurn()
        assert turn.questions == []

    def test_dialogue_turn_with_questions(self) -> None:
        """Test dialogue turn with questions."""
        turn = DocsInitDialogueTurn(questions=["q1", "q2"])
        assert turn.questions == ["q1", "q2"]

    def test_dialogue_payload_default_history(self) -> None:
        """Test default history for dialogue payload."""
        payload = DocsInitDialoguePayload()
        assert payload.history == []

    def test_apply_payload_default_files(self) -> None:
        """Test default files for apply payload."""
        payload = DocsInitApplyPayload()
        assert payload.files == []
        assert payload.mode == "minimal"
        assert payload.target_root == "docs"

    def test_docs_init_file_required_fields(self) -> None:
        """Test DocsInitFile requires path and content."""
        file = DocsInitFile(path="test.md", content="# Test")
        assert file.path == "test.md"
        assert file.content == "# Test"

    def test_docs_init_file_missing_path(self) -> None:
        """Test DocsInitFile requires path."""
        with pytest.raises(ValidationError):
            DocsInitFile(content="test")  # type: ignore[call-arg]


class TestAuditEventResponse:
    """Tests for AuditEventResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="hash1",
            signature="sig1",
        )
        assert event.event_id == "evt-1"
        assert event.version == "1.0"

    def test_default_version(self) -> None:
        """Test default version."""
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="hash1",
            signature="sig1",
        )
        assert event.version == "1.0"

    def test_default_dict_fields(self) -> None:
        """Test default dict fields."""
        event = AuditEventResponse(
            event_id="evt-1",
            timestamp="2024-01-01T00:00:00Z",
            event_type="test",
            prev_hash="hash1",
            signature="sig1",
        )
        assert event.source == {}
        assert event.task == {}
        assert event.data == {}


class TestAuditLogsResponse:
    """Tests for AuditLogsResponse schema."""

    def test_basic_creation(self) -> None:
        """Test basic creation."""
        resp = AuditLogsResponse(events=[], pagination={"page": 1})
        assert resp.events == []
        assert resp.pagination == {"page": 1}


class TestAuditExportParams:
    """Tests for AuditExportParams schema."""

    def test_default_format(self) -> None:
        """Test default format."""
        params = AuditExportParams()
        assert params.format == "json"

    def test_default_include_data(self) -> None:
        """Test default include_data."""
        params = AuditExportParams()
        assert params.include_data is True

    def test_csv_format(self) -> None:
        """Test CSV format."""
        params = AuditExportParams(format="csv")
        assert params.format == "csv"


class TestAuditVerifyResponse:
    """Tests for AuditVerifyResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        resp = AuditVerifyResponse(
            chain_valid=True,
            first_event_hash="hash1",
            last_event_hash="hash2",
            total_events=10,
            gap_count=0,
            verified_at="2024-01-01T00:00:00Z",
        )
        assert resp.chain_valid is True
        assert resp.total_events == 10

    def test_default_invalid_events(self) -> None:
        """Test default invalid_events."""
        resp = AuditVerifyResponse(
            chain_valid=True,
            first_event_hash="hash1",
            last_event_hash="hash2",
            total_events=10,
            gap_count=0,
            verified_at="2024-01-01T00:00:00Z",
        )
        assert resp.invalid_events == []


class TestAuditStatsResponse:
    """Tests for AuditStatsResponse schema."""

    def test_basic_creation(self) -> None:
        """Test basic creation."""
        resp = AuditStatsResponse(stats={}, time_range={"start": None, "end": None})
        assert resp.stats == {}


class TestAuditCleanupParams:
    """Tests for AuditCleanupParams schema."""

    def test_default_dry_run(self) -> None:
        """Test default dry_run."""
        params = AuditCleanupParams()
        assert params.dry_run is True

    def test_explicit_dry_run_false(self) -> None:
        """Test explicit dry_run false."""
        params = AuditCleanupParams(dry_run=False)
        assert params.dry_run is False


class TestAuditCleanupResponse:
    """Tests for AuditCleanupResponse schema."""

    def test_basic_creation(self) -> None:
        """Test basic creation."""
        resp = AuditCleanupResponse(
            would_delete=5,
            would_free_mb=10.5,
            dry_run=True,
            cutoff_date="2024-01-01",
        )
        assert resp.would_delete == 5
        assert resp.would_free_mb == 10.5

    def test_default_affected_files(self) -> None:
        """Test default affected_files."""
        resp = AuditCleanupResponse(
            would_delete=5,
            would_free_mb=10.5,
            dry_run=True,
            cutoff_date="2024-01-01",
        )
        assert resp.affected_files == []


class TestAuditTriageRequest:
    """Tests for AuditTriageRequest schema."""

    def test_all_optional(self) -> None:
        """Test all fields are optional."""
        req = AuditTriageRequest()
        assert req.run_id is None
        assert req.task_id is None
        assert req.trace_id is None

    def test_with_run_id(self) -> None:
        """Test with run_id."""
        req = AuditTriageRequest(run_id="run-1")
        assert req.run_id == "run-1"


class TestAuditTriageResponse:
    """Tests for AuditTriageResponse schema."""

    def test_required_status(self) -> None:
        """Test required status field."""
        resp = AuditTriageResponse(status="success", generated_at="2024-01-01T00:00:00Z")
        assert resp.status == "success"

    def test_default_lists(self) -> None:
        """Test default list fields."""
        resp = AuditTriageResponse(status="success", generated_at="2024-01-01T00:00:00Z")
        assert resp.pm_quality_history == []
        assert resp.leakage_findings == []
        assert resp.issues_fixed == []
        assert resp.next_risks == []

    def test_failure_hops_default_none(self) -> None:
        """Test failure_hops default is None."""
        resp = AuditTriageResponse(status="success", generated_at="2024-01-01T00:00:00Z")
        assert resp.failure_hops is None


class TestFailureHopsResponse:
    """Tests for FailureHopsResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        resp = FailureHopsResponse(
            run_id="run-1",
            generated_at="2024-01-01T00:00:00Z",
            ready=True,
            has_failure=False,
            failure_code="",
        )
        assert resp.schema_version == 2
        assert resp.run_id == "run-1"

    def test_default_schema_version(self) -> None:
        """Test default schema version."""
        resp = FailureHopsResponse(
            run_id="run-1",
            generated_at="2024-01-01T00:00:00Z",
            ready=True,
            has_failure=False,
            failure_code="",
        )
        assert resp.schema_version == 2


class TestFailureAnalysisRequest:
    """Tests for FailureAnalysisRequest schema."""

    def test_default_time_range(self) -> None:
        """Test default time_range."""
        req = FailureAnalysisRequest()
        assert req.time_range == "1h"

    def test_default_depth(self) -> None:
        """Test default depth."""
        req = FailureAnalysisRequest()
        assert req.depth == 3

    def test_depth_validation_min(self) -> None:
        """Test depth minimum validation."""
        with pytest.raises(ValidationError):
            FailureAnalysisRequest(depth=0)

    def test_depth_validation_max(self) -> None:
        """Test depth maximum validation."""
        with pytest.raises(ValidationError):
            FailureAnalysisRequest(depth=4)

    def test_valid_depth_boundary(self) -> None:
        """Test valid depth boundaries."""
        req1 = FailureAnalysisRequest(depth=1)
        assert req1.depth == 1
        req3 = FailureAnalysisRequest(depth=3)
        assert req3.depth == 3


class TestFailureAnalysisResponse:
    """Tests for FailureAnalysisResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        resp = FailureAnalysisResponse(
            depth=3,
            recommended_action="retry",
            failure_detected=False,
            event_count=0,
        )
        assert resp.depth == 3
        assert resp.failure_detected is False

    def test_default_lists(self) -> None:
        """Test default list fields."""
        resp = FailureAnalysisResponse(
            depth=3,
            recommended_action="retry",
            failure_detected=False,
            event_count=0,
        )
        assert resp.failure_hops == []
        assert resp.timeline == []


class TestProjectScanRequest:
    """Tests for ProjectScanRequest schema."""

    def test_default_scope(self) -> None:
        """Test default scope."""
        req = ProjectScanRequest()
        assert req.scope == "full"

    def test_default_max_files(self) -> None:
        """Test default max_files."""
        req = ProjectScanRequest()
        assert req.max_files == 800

    def test_max_files_validation(self) -> None:
        """Test max_files validation."""
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_files=0)
        with pytest.raises(ValidationError):
            ProjectScanRequest(max_files=5001)

    def test_valid_max_files_boundary(self) -> None:
        """Test valid max_files boundaries."""
        req1 = ProjectScanRequest(max_files=1)
        assert req1.max_files == 1
        req2 = ProjectScanRequest(max_files=5000)
        assert req2.max_files == 5000


class TestProjectScanResponse:
    """Tests for ProjectScanResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        resp = ProjectScanResponse(scope="full")
        assert resp.scope == "full"
        assert resp.focus == ""

    def test_default_lists(self) -> None:
        """Test default list fields."""
        resp = ProjectScanResponse(scope="full")
        assert resp.findings == []
        assert resp.recommendations == []


class TestCodeRegionRequest:
    """Tests for CodeRegionRequest schema."""

    def test_all_optional(self) -> None:
        """Test all fields are optional."""
        req = CodeRegionRequest()
        assert req.file_path is None
        assert req.function_name is None
        assert req.lines is None

    def test_with_file_path(self) -> None:
        """Test with file_path."""
        req = CodeRegionRequest(file_path="test.py")
        assert req.file_path == "test.py"


class TestCodeRegionResponse:
    """Tests for CodeRegionResponse schema."""

    def test_required_file(self) -> None:
        """Test required file field."""
        resp = CodeRegionResponse(file="test.py")
        assert resp.file == "test.py"
        assert resp.function_name == ""

    def test_default_dict_fields(self) -> None:
        """Test default dict fields."""
        resp = CodeRegionResponse(file="test.py")
        assert resp.line_range == {}
        assert resp.summary == {}
        assert resp.findings == []


class TestAuditTraceResponse:
    """Tests for AuditTraceResponse schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        resp = AuditTraceResponse(trace_id="trace-1", event_count=5)
        assert resp.trace_id == "trace-1"
        assert resp.event_count == 5

    def test_default_lists(self) -> None:
        """Test default list fields."""
        resp = AuditTraceResponse(trace_id="trace-1", event_count=5)
        assert resp.run_ids == []
        assert resp.task_ids == []
        assert resp.timeline == []

    def test_default_timestamps(self) -> None:
        """Test default timestamp fields."""
        resp = AuditTraceResponse(trace_id="trace-1", event_count=5)
        assert resp.first_timestamp == ""
        assert resp.last_timestamp == ""


class TestCorruptionRecord:
    """Tests for CorruptionRecord schema."""

    def test_required_fields(self) -> None:
        """Test required fields."""
        record = CorruptionRecord(
            timestamp="2024-01-01T00:00:00Z",
            file_path="test.log",
            offset=100,
            error_type="parse_error",
            error_message="Failed to parse",
            line_preview="bad line",
        )
        assert record.file_path == "test.log"
        assert record.offset == 100

    def test_default_recovered(self) -> None:
        """Test default recovered value."""
        record = CorruptionRecord(
            timestamp="2024-01-01T00:00:00Z",
            file_path="test.log",
            offset=100,
            error_type="parse_error",
            error_message="Failed to parse",
            line_preview="bad line",
        )
        assert record.recovered is False
