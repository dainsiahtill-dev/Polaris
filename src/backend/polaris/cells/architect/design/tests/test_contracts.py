"""Tests for architect.design public contracts."""

from __future__ import annotations

import pytest
from polaris.cells.architect.design.public.contracts import (
    ArchitectDesignError,
    ArchitectDesignErrorV1,
    ArchitectureDesignGeneratedEventV1,
    ArchitectureDesignResultV1,
    GenerateArchitectureDesignCommandV1,
    QueryArchitectureDesignStatusV1,
)

# ---------------------------------------------------------------------------
# GenerateArchitectureDesignCommandV1
# ---------------------------------------------------------------------------


class TestGenerateArchitectureDesignCommandV1:
    """GenerateArchitectureDesignCommandV1 validation and normalisation."""

    def test_required_fields(self) -> None:
        cmd = GenerateArchitectureDesignCommandV1(
            workspace="/tmp",
            objective="Design the auth module",
        )
        assert cmd.workspace == "/tmp"
        assert cmd.objective == "Design the auth module"
        assert cmd.constraints == {}
        assert cmd.context == {}

    def test_strips_whitespace(self) -> None:
        cmd = GenerateArchitectureDesignCommandV1(
            workspace="  /ws  ",
            objective="  Design something  ",
        )
        assert cmd.workspace == "/ws"
        assert cmd.objective == "Design something"

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GenerateArchitectureDesignCommandV1(workspace="", objective="Test")  # type: ignore[arg-type]

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            GenerateArchitectureDesignCommandV1(workspace="/tmp", objective="  ")  # type: ignore[arg-type]

    def test_constraints_normalised_to_dict(self) -> None:
        cmd = GenerateArchitectureDesignCommandV1(
            workspace="/tmp",
            objective="Test",
            constraints={"max_cost": 100},
        )
        assert isinstance(cmd.constraints, dict)
        assert cmd.constraints["max_cost"] == 100

    def test_context_normalised_to_dict(self) -> None:
        cmd = GenerateArchitectureDesignCommandV1(
            workspace="/tmp",
            objective="Test",
            context={"lang": "python"},
        )
        assert isinstance(cmd.context, dict)
        assert cmd.context["lang"] == "python"

    def test_constraints_defaults_to_empty_dict(self) -> None:
        cmd = GenerateArchitectureDesignCommandV1(workspace="/tmp", objective="Test")
        assert cmd.constraints == {}
        assert cmd.context == {}


# ---------------------------------------------------------------------------
# QueryArchitectureDesignStatusV1
# ---------------------------------------------------------------------------


class TestQueryArchitectureDesignStatusV1:
    """QueryArchitectureDesignStatusV1 validation."""

    def test_required_fields(self) -> None:
        q = QueryArchitectureDesignStatusV1(workspace="/tmp")
        assert q.workspace == "/tmp"
        assert q.design_id is None

    def test_with_design_id(self) -> None:
        q = QueryArchitectureDesignStatusV1(workspace="/tmp", design_id="d-123")
        assert q.design_id == "d-123"

    def test_strips_whitespace_from_design_id(self) -> None:
        q = QueryArchitectureDesignStatusV1(workspace="/tmp", design_id="  d-456  ")
        assert q.design_id == "d-456"

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QueryArchitectureDesignStatusV1(workspace="   ")  # type: ignore[arg-type]

    def test_empty_design_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            QueryArchitectureDesignStatusV1(workspace="/tmp", design_id="")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ArchitectureDesignGeneratedEventV1
# ---------------------------------------------------------------------------


class TestArchitectureDesignGeneratedEventV1:
    """ArchitectureDesignGeneratedEventV1 validation."""

    def test_required_fields(self) -> None:
        evt = ArchitectureDesignGeneratedEventV1(
            event_id="e-1",
            workspace="/tmp",
            design_id="d-1",
            output_path="/tmp/design.md",
            generated_at="2026-01-01T00:00:00Z",
        )
        assert evt.event_id == "e-1"
        assert evt.design_id == "d-1"
        assert evt.output_path == "/tmp/design.md"

    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectureDesignGeneratedEventV1(
                event_id="",
                workspace="/tmp",
                design_id="d-1",
                output_path="/tmp/out.md",
                generated_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]

    def test_empty_design_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectureDesignGeneratedEventV1(
                event_id="e-1",
                workspace="/tmp",
                design_id="  ",
                output_path="/tmp/out.md",
                generated_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]

    def test_empty_output_path_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectureDesignGeneratedEventV1(
                event_id="e-1",
                workspace="/tmp",
                design_id="d-1",
                output_path="",
                generated_at="2026-01-01T00:00:00Z",
            )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ArchitectureDesignResultV1
# ---------------------------------------------------------------------------


class TestArchitectureDesignResultV1:
    """ArchitectureDesignResultV1 validation."""

    def test_ok_result(self) -> None:
        r = ArchitectureDesignResultV1(
            ok=True,
            workspace="/tmp",
            design_id="d-1",
            status="completed",
            summary="Design generated successfully",
        )
        assert r.ok is True
        assert r.status == "completed"
        assert r.summary == "Design generated successfully"
        assert r.recommendation_paths == ()

    def test_recommendation_paths_filtered(self) -> None:
        r = ArchitectureDesignResultV1(
            ok=True,
            workspace="/tmp",
            design_id="d-1",
            status="completed",
            recommendation_paths=["/a.py", "  ", "/b.py"],  # type: ignore[arg-type]
        )
        assert r.recommendation_paths == ("/a.py", "/b.py")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectureDesignResultV1(
                ok=True,
                workspace="",
                design_id="d-1",
                status="completed",
            )  # type: ignore[arg-type]

    def test_empty_design_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectureDesignResultV1(
                ok=True,
                workspace="/tmp",
                design_id="   ",
                status="completed",
            )  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ArchitectDesignErrorV1
# ---------------------------------------------------------------------------


class TestArchitectDesignErrorV1:
    """ArchitectDesignErrorV1 structured error."""

    def test_default_code(self) -> None:
        err = ArchitectDesignErrorV1("something went wrong")
        assert err.code == "architect_design_error"
        assert str(err) == "something went wrong"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ArchitectDesignErrorV1(
            "boom",
            code="VALIDATION_FAILED",
            details={"field": "objective"},
        )
        assert err.code == "VALIDATION_FAILED"
        assert err.details["field"] == "objective"

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectDesignErrorV1("")  # type: ignore[arg-type]

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            ArchitectDesignErrorV1("msg", code="  ")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Backward-compatible alias
# ---------------------------------------------------------------------------


class TestArchitectDesignErrorAlias:
    """Alias ArchitectDesignError must be identical to ArchitectDesignErrorV1."""

    def test_alias_is_same_class(self) -> None:
        assert ArchitectDesignError is ArchitectDesignErrorV1

    def test_alias_instantiation(self) -> None:
        err = ArchitectDesignError("alias test")
        assert isinstance(err, ArchitectDesignErrorV1)
        assert isinstance(err, ArchitectDesignError)
        assert err.code == "architect_design_error"
