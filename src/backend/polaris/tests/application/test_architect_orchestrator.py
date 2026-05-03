"""Tests for polaris.application.orchestration.architect_orchestrator module.

Covers:
- ArchitectOrchestrator: gather_context, design_requirements, design_adr,
  design_interface_contract, design_implementation_plan, compile_blueprint,
  build_handoff_package, run_design_lifecycle
- Error handling and edge cases
- Service resolution errors
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from polaris.application.orchestration.architect_orchestrator import (
    ArchitectDesignConfig,
    ArchitectDesignLifecycleResult,
    ArchitectOrchestrator,
    ArchitectOrchestratorError,
    BlueprintResult,
    DesignResult,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Test fixtures
# =============================================================================


@pytest.fixture
def config() -> ArchitectDesignConfig:
    """Create a basic architect design config."""
    return ArchitectDesignConfig(workspace="/tmp/test-workspace")


@pytest.fixture
def orchestrator(config: ArchitectDesignConfig) -> ArchitectOrchestrator:
    """Create an ArchitectOrchestrator instance."""
    return ArchitectOrchestrator(config)


@pytest.fixture
def mock_doc() -> MagicMock:
    """Create a mock document returned from architect service."""
    doc = MagicMock()
    doc.doc_id = "doc-123"
    doc.doc_type = "requirements"
    doc.title = "Test Requirements"
    doc.content = "Content for the requirements document."
    doc.version = "1.0"
    doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))
    return doc


# =============================================================================
# Tests for ArchitectOrchestrator construction
# =============================================================================


class TestArchitectOrchestratorInit:
    """Tests for ArchitectOrchestrator initialization."""

    def test_init_stores_config(self, config: ArchitectDesignConfig) -> None:
        """Verify config is stored correctly."""
        orch = ArchitectOrchestrator(config)
        assert orch._config is config
        assert orch._workspace == "/tmp/test-workspace"

    def test_init_default_service_is_none(self, config: ArchitectDesignConfig) -> None:
        """Verify lazy service is initially None."""
        orch = ArchitectOrchestrator(config)
        assert orch._architect_service is None


# =============================================================================
# Tests for gather_context
# =============================================================================


class TestGatherContext:
    """Tests for the gather_context method."""

    def test_gather_context_empty_objective_raises(self, config: ArchitectDesignConfig) -> None:
        """Verify empty objective raises ArchitectOrchestratorError."""
        orch = ArchitectOrchestrator(config)
        with pytest.raises(ArchitectOrchestratorError, match="design objective is required"):
            orch.gather_context()

    def test_gather_context_from_parameter(self, config: ArchitectDesignConfig) -> None:
        """Verify objective can be passed as parameter."""
        orch = ArchitectOrchestrator(config)
        ctx = orch.gather_context(objective="test objective")
        assert ctx["objective"] == "test objective"

    def test_gather_context_merges_constraints(self, config: ArchitectDesignConfig) -> None:
        """Verify constraints are merged from config and parameter."""
        orch = ArchitectOrchestrator(
            ArchitectDesignConfig(
                workspace="/tmp",
                constraints={"k1": "v1"},
            )
        )
        ctx = orch.gather_context(
            objective="test",
            constraints={"k2": "v2"},
        )
        assert ctx["constraints"]["k1"] == "v1"
        assert ctx["constraints"]["k2"] == "v2"

    def test_gather_context_merges_context(self, config: ArchitectDesignConfig) -> None:
        """Verify context is merged from config and parameter."""
        orch = ArchitectOrchestrator(
            ArchitectDesignConfig(
                workspace="/tmp",
                context={"ck1": "cv1"},
            )
        )
        ctx = orch.gather_context(
            objective="test",
            context={"ck2": "cv2"},
        )
        assert ctx["context"]["ck1"] == "cv1"
        assert ctx["context"]["ck2"] == "cv2"

    def test_gather_context_returns_workspace(self, config: ArchitectDesignConfig) -> None:
        """Verify workspace is included in context."""
        orch = ArchitectOrchestrator(config)
        ctx = orch.gather_context(objective="test")
        assert ctx["workspace"] == "/tmp/test-workspace"

    def test_gather_context_strips_whitespace(self, config: ArchitectDesignConfig) -> None:
        """Verify objective whitespace is stripped."""
        orch = ArchitectOrchestrator(config)
        ctx = orch.gather_context(objective="  test objective  ")
        assert ctx["objective"] == "test objective"


# =============================================================================
# Tests for design_requirements
# =============================================================================


class TestDesignRequirements:
    """Tests for the design_requirements method."""

    @pytest.mark.asyncio
    async def test_design_requirements_success(
        self,
        orchestrator: ArchitectOrchestrator,
        mock_doc: MagicMock,
    ) -> None:
        """Verify successful requirements design."""
        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_requirements(
                goal="Test goal",
                in_scope=["item1"],
                out_of_scope=["item2"],
                constraints=["constraint1"],
                definition_of_done=["done1"],
                backlog=["backlog1"],
            )

            assert result.design_id == "doc-123"
            assert result.doc_type == "requirements"
            assert result.title == "Test Requirements"
            assert result.status == "completed"
            assert result.content_length > 0

    @pytest.mark.asyncio
    async def test_design_requirements_none_params_handled(
        self, orchestrator: ArchitectOrchestrator, mock_doc: MagicMock
    ) -> None:
        """Verify None parameters are handled gracefully."""
        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_requirements(
                goal="test",
                in_scope=None,  # type: ignore
                out_of_scope=None,  # type: ignore
                constraints=None,  # type: ignore
                definition_of_done=None,  # type: ignore
                backlog=None,  # type: ignore
            )

            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_requirements_service_error(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify service errors are wrapped correctly."""
        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            with pytest.raises(ArchitectOrchestratorError) as exc_info:
                await orchestrator.design_requirements(
                    goal="test",
                    in_scope=[],
                    out_of_scope=[],
                    constraints=[],
                    definition_of_done=[],
                    backlog=[],
                )

            assert "Requirements design failed" in str(exc_info.value)
            assert exc_info.value.code == "requirements_design_failed"


# =============================================================================
# Tests for design_adr
# =============================================================================


class TestDesignAdr:
    """Tests for the design_adr method."""

    @pytest.mark.asyncio
    async def test_design_adr_success(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify successful ADR design."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "adr-456"
        mock_doc.doc_type = "adr"
        mock_doc.title = "Use JSON for config"
        mock_doc.content = "Decision: Use JSON"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_adr = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_adr(
                title="Use JSON for config",
                context="We need a config format",
                decision="Use JSON",
                consequences=["Simple parsing"],
            )

            assert result.design_id == "adr-456"
            assert result.doc_type == "adr"
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_adr_service_error(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify ADR errors are wrapped correctly."""
        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_adr = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            with pytest.raises(ArchitectOrchestratorError) as exc_info:
                await orchestrator.design_adr(
                    title="test",
                    context="context",
                    decision="decision",
                    consequences=[],
                )

            assert exc_info.value.code == "adr_design_failed"


# =============================================================================
# Tests for design_interface_contract
# =============================================================================


class TestDesignInterfaceContract:
    """Tests for the design_interface_contract method."""

    @pytest.mark.asyncio
    async def test_design_interface_contract_success(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify successful interface contract design."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "iface-789"
        mock_doc.doc_type = "interface_contract"
        mock_doc.title = "User API"
        mock_doc.content = '{"endpoints": []}'
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_interface_contract = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_interface_contract(
                api_name="UserAPI",
                endpoints=[{"path": "/users", "method": "GET"}],
            )

            assert result.design_id == "iface-789"
            assert result.doc_type == "interface_contract"
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_interface_contract_empty_endpoints(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify empty endpoints list is handled."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "iface-empty"
        mock_doc.doc_type = "interface_contract"
        mock_doc.title = "Empty API"
        mock_doc.content = "{}"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_interface_contract = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_interface_contract(
                api_name="EmptyAPI",
                endpoints=[],
            )

            assert result.status == "completed"


# =============================================================================
# Tests for design_implementation_plan
# =============================================================================


class TestDesignImplementationPlan:
    """Tests for the design_implementation_plan method."""

    @pytest.mark.asyncio
    async def test_design_implementation_plan_success(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify successful implementation plan design."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "plan-101"
        mock_doc.doc_type = "plan"
        mock_doc.title = "Phase 1 Plan"
        mock_doc.content = '{"milestones": []}'
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_implementation_plan = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_implementation_plan(
                milestones=["M1", "M2"],
                verification_commands=["pytest"],
                risks=[{"risk": "delay", "mitigation": "add buffer"}],
            )

            assert result.design_id == "plan-101"
            assert result.doc_type == "plan"
            assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_design_implementation_plan_empty_risks(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify empty risks list is handled."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "plan-no-risks"
        mock_doc.doc_type = "plan"
        mock_doc.title = "No Risks Plan"
        mock_doc.content = "{}"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_implementation_plan = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.design_implementation_plan(
                milestones=["M1"],
                verification_commands=["test"],
                risks=[],
            )

            assert result.status == "completed"


# =============================================================================
# Tests for compile_blueprint
# =============================================================================


class TestCompileBlueprint:
    """Tests for the compile_blueprint method."""

    def test_compile_blueprint_all_completed(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify blueprint status is ready when all designs complete."""
        dr1 = DesignResult(design_id="d1", doc_type="req", title="t", status="completed")
        dr2 = DesignResult(design_id="d2", doc_type="adr", title="t2", status="completed")

        bp = orchestrator.compile_blueprint(designs=[dr1, dr2])

        assert bp.status == "ready"
        assert bp.design_ids == ("d1", "d2")

    def test_compile_blueprint_partial_failure(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify blueprint status is incomplete with partial failures."""
        dr1 = DesignResult(design_id="d1", doc_type="req", title="t", status="completed")
        dr2 = DesignResult(design_id="d2", doc_type="adr", title="t2", status="failed")

        bp = orchestrator.compile_blueprint(designs=[dr1, dr2])

        assert bp.status == "incomplete"
        assert bp.design_ids == ("d1",)

    def test_compile_blueprint_all_failed(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify blueprint status is failed when all designs fail."""
        dr1 = DesignResult(design_id="d1", doc_type="req", title="t", status="failed")
        dr2 = DesignResult(design_id="d2", doc_type="adr", title="t2", status="failed")

        bp = orchestrator.compile_blueprint(designs=[dr1, dr2])

        assert bp.status == "failed"
        assert bp.design_ids == ()

    def test_compile_blueprint_empty_designs(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify blueprint status is failed with empty designs."""
        bp = orchestrator.compile_blueprint(designs=[])

        assert bp.status == "failed"
        assert bp.design_ids == ()

    def test_compile_blueprint_with_summary(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify custom summary is used."""
        dr = DesignResult(design_id="d1", doc_type="req", title="t", status="completed")

        bp = orchestrator.compile_blueprint(designs=[dr], summary="Custom summary")

        assert bp.summary == "Custom summary"

    def test_compile_blueprint_collects_output_paths(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify output paths are collected from designs."""
        dr = DesignResult(
            design_id="d1",
            doc_type="req",
            title="t",
            status="completed",
            output_path="/docs/req.md",
        )

        bp = orchestrator.compile_blueprint(designs=[dr])

        assert "/docs/req.md" in bp.recommendation_paths


# =============================================================================
# Tests for build_handoff_package
# =============================================================================


class TestBuildHandoffPackage:
    """Tests for the build_handoff_package method."""

    def test_build_handoff_package_structure(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify handoff package has correct structure."""
        dr = DesignResult(
            design_id="d1",
            doc_type="req",
            title="Requirements",
            status="completed",
            content_length=100,
        )
        bp = BlueprintResult(
            blueprint_id="bp-1",
            design_ids=("d1",),
            summary="Test Blueprint",
            recommendation_paths=("/docs/req.md",),
            status="ready",
        )

        pkg = orchestrator.build_handoff_package(blueprint=bp, designs=[dr])

        assert pkg["handoff_type"] == "architect_blueprint"
        assert pkg["workspace"] == "/tmp/test-workspace"
        assert pkg["blueprint_id"] == "bp-1"
        assert pkg["blueprint_status"] == "ready"
        assert len(pkg["designs"]) == 1
        assert pkg["designs"][0]["design_id"] == "d1"

    def test_build_handoff_package_with_extra_metadata(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify extra metadata is merged into handoff package."""
        dr = DesignResult(design_id="d1", doc_type="req", title="t", status="completed")
        bp = BlueprintResult(
            blueprint_id="bp-1",
            design_ids=("d1",),
            summary="Test",
            status="ready",
        )

        pkg = orchestrator.build_handoff_package(
            blueprint=bp,
            designs=[dr],
            extra={"priority": "high"},
        )

        assert pkg["metadata"]["priority"] == "high"


# =============================================================================
# Tests for lazy service resolution
# =============================================================================


class TestServiceResolution:
    """Tests for lazy service resolution."""

    def test_get_architect_service_import_error(self, config: ArchitectDesignConfig) -> None:
        """Verify ImportError is wrapped correctly."""
        orch = ArchitectOrchestrator(config)

        with patch("builtins.__import__", side_effect=ImportError("Module not found")):
            with pytest.raises(ArchitectOrchestratorError) as exc_info:
                orch._get_architect_service()

            assert exc_info.value.code == "architect_service_resolution_error"

    def test_get_architect_service_caches_service(self, config: ArchitectDesignConfig) -> None:
        """Verify service is cached after first call."""
        orch = ArchitectOrchestrator(config)

        with patch.object(orch, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_get.return_value = mock_service

            # First call
            service1 = orch._get_architect_service()
            # Second call should return cached service
            service2 = orch._get_architect_service()

            assert service1 is service2
            assert mock_get.call_count == 2  # Called twice because mock replaces method


# =============================================================================
# Tests for run_design_lifecycle
# =============================================================================


class TestRunDesignLifecycle:
    """Tests for the run_design_lifecycle convenience method."""

    @pytest.mark.asyncio
    async def test_run_design_lifecycle_success(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify full design lifecycle completes successfully."""
        mock_doc = MagicMock()
        mock_doc.doc_id = "lifecycle-doc"
        mock_doc.doc_type = "requirements"
        mock_doc.title = "Lifecycle Test"
        mock_doc.content = "Content"
        mock_doc.version = "1.0"
        mock_doc.created_at = MagicMock(spec=["isoformat"], isoformat=MagicMock(return_value="2026-05-01T00:00:00"))

        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(return_value=mock_doc)
            mock_get.return_value = mock_service

            result = await orchestrator.run_design_lifecycle(
                objective="Test objective",
                requirements={
                    "goal": "Test goal",
                    "in_scope": ["item1"],
                    "out_of_scope": [],
                    "constraints": [],
                    "definition_of_done": [],
                    "backlog": [],
                },
            )

            assert isinstance(result, ArchitectDesignLifecycleResult)
            assert result.success is True
            assert len(result.designs) == 1
            assert result.blueprint is not None

    @pytest.mark.asyncio
    async def test_run_design_lifecycle_handles_design_failure(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify lifecycle handles design failures gracefully."""
        with patch.object(orchestrator, "_get_architect_service") as mock_get:
            mock_service = MagicMock()
            mock_service.create_requirements_doc = AsyncMock(side_effect=RuntimeError("boom"))
            mock_get.return_value = mock_service

            result = await orchestrator.run_design_lifecycle(
                objective="Test objective",
                requirements={
                    "goal": "Test goal",
                    "in_scope": [],
                    "out_of_scope": [],
                    "constraints": [],
                    "definition_of_done": [],
                    "backlog": [],
                },
            )

            # Should still return a result with failed design
            assert len(result.designs) == 1
            assert result.designs[0].status == "failed"
            assert result.blueprint is not None

    @pytest.mark.asyncio
    async def test_run_design_lifecycle_empty_objective_raises(self, orchestrator: ArchitectOrchestrator) -> None:
        """Verify empty objective raises error in lifecycle."""
        with pytest.raises(ArchitectOrchestratorError, match="design objective is required"):
            await orchestrator.run_design_lifecycle(objective="")


# =============================================================================
# Tests for value objects
# =============================================================================


class TestDesignResult:
    """Tests for DesignResult value object."""

    def test_design_result_has_dataclass_fields(self) -> None:
        """Verify DesignResult has expected dataclass fields."""
        import dataclasses

        assert dataclasses.is_dataclass(DesignResult)
        dr = DesignResult(
            design_id="d1",
            doc_type="req",
            title="Test",
            status="completed",
        )

        # Verify frozen dataclass by checking frozen attribute
        assert getattr(type(dr), "__dataclass_params__", None) is not None

    def test_design_result_default_values(self) -> None:
        """Verify default values are set correctly."""
        dr = DesignResult(
            design_id="d1",
            doc_type="req",
            title="Test",
            status="completed",
        )

        assert dr.content_length == 0
        assert dr.output_path == ""
        assert dr.error == ""
        assert dr.metadata == {}


class TestBlueprintResult:
    """Tests for BlueprintResult value object."""

    def test_blueprint_result_default_status(self) -> None:
        """Verify default status is 'ready'."""
        import dataclasses

        bp = BlueprintResult(
            blueprint_id="bp-1",
            design_ids=("d1",),
            summary="Test",
        )

        assert bp.status == "ready"
        assert dataclasses.is_dataclass(BlueprintResult)


class TestArchitectDesignConfig:
    """Tests for ArchitectDesignConfig value object."""

    def test_config_default_values(self) -> None:
        """Verify default config values."""
        config = ArchitectDesignConfig(workspace="/tmp/ws")

        assert config.docs_dir == "docs/product"
        assert config.objective == ""
        assert config.constraints == {}
        assert config.context == {}

    def test_config_custom_values(self) -> None:
        """Verify custom config values."""
        config = ArchitectDesignConfig(
            workspace="/tmp/ws",
            docs_dir="custom/docs",
            objective="Test objective",
            constraints={"key": "value"},
            context={"ctx": "data"},
        )

        assert config.docs_dir == "custom/docs"
        assert config.objective == "Test objective"
        assert config.constraints == {"key": "value"}
        assert config.context == {"ctx": "data"}
