"""Tests for CE consumer (ce_consumer.py)."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer


class TestCEConsumerInit:
    def test_valid_construction(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(workspace="/test/workspace", worker_id="w1")
            assert consumer._workspace == "/test/workspace"
            assert consumer._worker_id == "w1"
            assert consumer._visibility_timeout == 900
            assert isinstance(consumer._work_event, threading.Event)

    def test_custom_params(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(
                workspace="/test",
                worker_id="custom_worker",
                visibility_timeout_seconds=300,
                poll_interval=10.0,
            )
            assert consumer._visibility_timeout == 300
            assert isinstance(consumer._work_event, threading.Event)

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CEConsumer(workspace="", worker_id="w1")

    def test_whitespace_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            CEConsumer(workspace="   ", worker_id="w1")

    def test_empty_worker_id_raises(self) -> None:
        with pytest.raises(ValueError, match="worker_id"):
            CEConsumer(workspace="/test", worker_id="")

    def test_stop_event_initial_state(self) -> None:
        with patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service") as mock_get:
            mock_get.return_value = MagicMock()
            consumer = CEConsumer(workspace="/test", worker_id="w1")
            assert consumer._stop_event is not None
            assert not consumer._stop_event.is_set()


class TestCEConsumerPollOnce:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_no_claimable_tasks_returns_empty_list(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        mock_result = MagicMock()
        mock_result.ok = False
        mock_result.task_id = ""
        mock_result.lease_token = ""
        mock_svc.claim_work_item.return_value = mock_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()
        assert results == []

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_successful_claim_and_ack(self, mock_get_svc: MagicMock) -> None:
        """Verify claim/ack flow by patching _run_ce_preflight to avoid actual CE analysis."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        # Claim returns a task on first call, then a mock with ok=False to break loop
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-42"
        claim_result.lease_token = "lease-abc"
        claim_result.payload = {
            "title": "Test task",
            "description": (
                "Build a complex realtime service with WebSocket updates, "
                "database persistence, and dependency injection."
            ),
            "scope_paths": ["/src/main.py"],
            "acceptance_criteria": ["Main module is generated"],
            "execution_checklist": ["Create main module", "Verify import"],
            "constraints": {"layer": "application"},
            "pm_contract": {
                "id": "task-42",
                "acceptance_criteria": ["Main module is generated"],
                "execution_checklist": ["Create main module", "Verify import"],
            },
        }

        # Ack returns success
        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"

        # side_effect: first call returns claim_result, second call returns mock with ok=False
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")

        with patch.object(
            consumer,
            "_run_ce_preflight",
            return_value={
                "blueprint_id": "bp-task-42",
                "guardrails": ["rule1"],
                "no_touch_zones": ["zone1"],
            },
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-42"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        # Verify ack was called with correct next_stage
        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.next_stage == "pending_exec"
        assert cmd.metadata["blueprint_id"] == "bp-task-42"
        assert cmd.metadata["blueprint_path"] == "runtime/blueprints/bp-task-42.json"
        assert cmd.metadata["runtime_blueprint_path"] == "runtime/blueprints/bp-task-42.json"
        assert cmd.metadata["blueprint_hash"]
        assert cmd.metadata["contract_hash"]
        assert cmd.metadata["job_token"]["token_id"] == cmd.metadata["job_token_id"]
        assert cmd.metadata["job_token"]["contract_hash"] == cmd.metadata["contract_hash"]
        assert cmd.metadata["job_token"]["blueprint_hash"] == cmd.metadata["blueprint_hash"]
        assert cmd.metadata["job_token"]["allowed_write_paths"] == ["/src/main.py"]
        assert cmd.metadata["job_token"]["allowed_read_paths"] == ["/src/main.py"]
        gate_policy = cmd.metadata["job_token"]["gate_policy"]
        assert {"qa", "code", "tool_receipt"} <= set(gate_policy["required_evidence_modalities"])
        assert gate_policy["evidence_policy_compiler"]["ok"] is True
        assert gate_policy["compiled_evidence_policy_hash"]
        assert cmd.metadata["control_plane_lineage"]["blueprint_hash"] == cmd.metadata["blueprint_hash"]
        assert cmd.metadata["route"] == "chief_blueprint_required"
        assert cmd.metadata["blueprint_required"] is True
        assert cmd.metadata["target_files"] == ["/src/main.py"]
        assert cmd.metadata["acceptance_criteria"] == ["Main module is generated"]
        assert cmd.metadata["execution_checklist"] == ["Create main module", "Verify import"]
        assert cmd.metadata["constraints"] == {"layer": "application"}
        assert cmd.metadata["pm_contract"]["id"] == "task-42"
        assert cmd.metadata["contract_completeness"]["handoff_ready"] is True
        concerns = {item["concern"] for item in cmd.metadata["architecture_decisions"]}
        assert concerns >= {"application_architecture", "realtime", "database"}
        assert cmd.metadata["selected_libraries"] == []
        database = next(item for item in cmd.metadata["architecture_decisions"] if item["concern"] == "database")
        assert database["evidence"]["guidance_only"] is True
        assert database["decision_status"] == "guidance"
        assert database["source"] == "platform_signal_guidance"
        assert any("vector" in item.lower() for item in database["options_considered"])

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_claim_preserves_interface_discrepancy_context_in_blueprint_handoff(
        self,
        mock_get_svc: MagicMock,
        tmp_path: Path,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        amendment_request = {
            "schema_version": "cross_artifact.contract_amendment_request.v1",
            "task_id": "task-contract",
            "requested_by": "director",
            "reason": "consumer imports symbols the owner task never exported",
        }
        interface_context = {
            "schema_version": "interface_discrepancy.context.v1",
            "recommended_owner": "chief_engineer",
            "interface_delta": [
                {
                    "owner_file": "src/models/weather.py",
                    "consumer_file": "src/engine/forecast.py",
                    "missing_symbols": ["Weather", "WeatherKind"],
                }
            ],
        }
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-contract"
        claim_result.lease_token = "lease-contract"
        claim_result.payload = {
            "title": "Fix cross-file interface contract",
            "description": "Align weather model exports with forecast consumers.",
            "target_files": ["src/models/weather.py", "src/engine/forecast.py"],
            "scope_paths": ["src/models/weather.py", "src/engine/forecast.py"],
            "acceptance_criteria": ["imports resolve"],
            "execution_checklist": ["Update owner module exports", "Update consumer imports"],
            "amendment_request": amendment_request,
            "interface_discrepancy_context": interface_context,
            "requeue_context": {
                "schema_version": "task_market.requeue_context.v1",
                "keys": ["amendment_request", "interface_discrepancy_context"],
            },
        }
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = CEConsumer(
            workspace=str(tmp_path),
            worker_id="w1",
            enable_director_pool=False,
        )

        with patch.object(
            consumer,
            "_run_ce_preflight",
            return_value={
                "blueprint_id": "bp-task-contract",
                "target_files": ["src/models/weather.py", "src/engine/forecast.py"],
                "scope_paths": ["src/models/weather.py", "src/engine/forecast.py"],
            },
        ) as preflight:
            results = consumer.poll_once()

        assert results[0]["ok"] is True
        preflight_payload = preflight.call_args[0][1]
        assert preflight_payload["amendment_request"] == amendment_request
        assert preflight_payload["interface_discrepancy_context"] == interface_context

        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.metadata["amendment_request"] == amendment_request
        assert cmd.metadata["interface_discrepancy_context"] == interface_context
        assert cmd.metadata["requeue_context"] == {
            "schema_version": "task_market.requeue_context.v1",
            "keys": ["amendment_request", "interface_discrepancy_context"],
        }

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_successful_claim_preserves_pm_task_test_targets(self, mock_get_svc: MagicMock) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-js"
        claim_result.lease_token = "lease-js"
        claim_result.payload = {
            "title": "JavaScript task",
            "description": "Build JavaScript source and tests.",
            "target_files": ["src/index.js"],
            "acceptance_criteria": ["npm test passes"],
            "execution_checklist": ["Implement source", "Add tests", "Run npm test"],
            "pm_contract": {
                "id": "task-js",
                "target_files": [
                    "src/index.js",
                    "src/engine/rules.js",
                    "tests/product.test.js",
                    "tests/test_product.py",
                    "README.md",
                ],
                "scope_paths": ["src/index.js", "tests/product.test.js"],
                "acceptance_criteria": ["npm test passes"],
                "execution_checklist": ["Implement source", "Add tests", "Run npm test"],
            },
        }
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")

        with patch.object(
            consumer,
            "_run_ce_preflight",
            return_value={
                "blueprint_id": "bp-task-js",
                "target_files": ["src/index.js"],
                "scope_paths": ["src/index.js"],
            },
        ):
            results = consumer.poll_once()

        assert results[0]["ok"] is True
        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.metadata["target_files"] == [
            "src/index.js",
            "src/engine/rules.js",
            "tests/product.test.js",
            "tests/test_product.py",
            "README.md",
        ]
        assert cmd.metadata["job_token"]["target_files"] == cmd.metadata["target_files"]
        assert cmd.metadata["job_token"]["allowed_write_paths"] == cmd.metadata["target_files"]
        assert "tests/product.test.js" in cmd.metadata["job_token"]["allowed_paths"]
        assert "tests/product.test.js" in cmd.metadata["job_token"]["allowed_read_paths"]
        assert "tests/test_product.py" in cmd.metadata["job_token"]["required_artifacts"]

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_claim_then_preflight_failure_requeues(self, mock_get_svc: MagicMock) -> None:
        """Verify failure path requeues to pending_design."""
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-99"
        claim_result.lease_token = "lease-xyz"
        claim_result.payload = {"title": "Failing task"}

        fail_result = MagicMock()
        fail_result.ok = False

        # First call returns the claim, second returns mock with ok=False to break loop
        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]
        mock_svc.fail_task_stage.return_value = fail_result

        consumer = CEConsumer(workspace="/test", worker_id="w1")

        with patch.object(
            consumer,
            "_run_ce_preflight",
            side_effect=RuntimeError("analysis runner missing"),
        ):
            results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-99"
        assert results[0]["ok"] is False
        assert "analysis runner missing" in results[0]["reason"]

        # Verify requeue happened with correct error code
        fail_call = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_call.requeue_stage == "pending_design"
        assert fail_call.error_code == "CE_design_failed"


class TestStepSplitterIntegration:
    """I3-r29: an over-budget step is split and the skeleton+fills flow through the
    real publish path with edit_on_prior preserved (review finding 3)."""

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_split_output_publishes_skeleton_and_fills(self, mock_get: MagicMock) -> None:
        from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
            normalize_construction_step,
            validate_construction_steps,
        )
        from polaris.cells.chief_engineer.blueprint.internal.step_splitter import split_oversize_steps

        mock_get.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        sigs = [f"function f{i}()" for i in range(12)]
        oversize = [
            normalize_construction_step(
                {
                    "step_id": "S4",
                    "target_file": "main.js",
                    "signatures": sigs,
                    "est_lines": 200,
                    "verify": "node --check main.js",
                },
                parent_pm_task="PM-0001-1",
                index=0,
            )
        ]
        split = split_oversize_steps(oversize, parent_pm_task="PM-0001-1")
        # The split the consumer adopts is always gate-clean (re-gate invariant).
        assert validate_construction_steps(split, parent_pm_task="PM-0001-1") == []

        consumer._publish_step_tasks(
            "PM-0001-1",
            {
                "run_id": "r",
                "blueprint_hash": "bh",
                "contract_hash": "ch",
                "job_token_id": "job-1",
                "job_token": {"token_id": "job-1", "contract_hash": "ch", "blueprint_hash": "bh"},
                "control_plane_lineage": {"blueprint_hash": "bh"},
                "architecture_decisions": [
                    {
                        "concern": "application_architecture",
                        "decision": "Use layered architecture with dependency injection.",
                        "selected_libraries": ["Layered Architecture", "Dependency Injection"],
                    }
                ],
                "selected_libraries": ["Layered Architecture", "Dependency Injection"],
            },
            split,
            blueprint_id="b",
            blueprint_path="p",
            prior_file_owners={},
        )
        published = [c.args[0].payload["construction_step"] for c in consumer._svc.publish_work_item.call_args_list]
        first_command = consumer._svc.publish_work_item.call_args_list[0].args[0]
        assert first_command.payload["job_token"]["token_id"] == "job-1"
        assert first_command.payload["blueprint_hash"] == "bh"
        assert first_command.metadata["job_token"]["token_id"] == "job-1"
        assert first_command.payload["architecture_decisions"][0]["concern"] == "application_architecture"
        assert first_command.metadata["selected_libraries"] == ["Layered Architecture", "Dependency Injection"]
        ids = [s["step_id"] for s in published]
        assert "PM-0001-1-S4-skel" in ids
        assert sum(1 for i in ids if "-fill" in i) == 4
        # fills publish with edit_on_prior preserved (publish path must not strip it).
        fills = [s for s in published if "-fill" in s["step_id"]]
        assert fills and all(s.get("edit_on_prior") is True for s in fills)


class TestCEConsumerStop:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_stop_sets_event(self, mock_get_svc: MagicMock) -> None:
        mock_get_svc.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        assert not consumer._stop_event.is_set()
        consumer.stop()
        assert consumer._stop_event.is_set()


class TestCEFissionMaxOutputTokens:
    """The CE step-fission output budget (I3-r17): reasoning-sized floor, env-tunable."""

    def test_default_is_reasoning_sized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import _ce_fission_max_output_tokens

        monkeypatch.delenv("KERNELONE_CE_FISSION_MAX_TOKENS", raising=False)
        # maximum platform floor so large CE fission JSON is not truncated.
        assert _ce_fission_max_output_tokens() == 128_000

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import _ce_fission_max_output_tokens

        monkeypatch.setenv("KERNELONE_CE_FISSION_MAX_TOKENS", "20000")
        assert _ce_fission_max_output_tokens() == 20000

    def test_invalid_env_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import _ce_fission_max_output_tokens

        monkeypatch.setenv("KERNELONE_CE_FISSION_MAX_TOKENS", "not-a-number")
        assert _ce_fission_max_output_tokens() == 128_000
        monkeypatch.setenv("KERNELONE_CE_FISSION_MAX_TOKENS", "0")
        assert _ce_fission_max_output_tokens() == 128_000


class TestCrossParentFileOwnershipInjection:
    """I3-r18 FIX-1: a step writing a file owned by an EARLIER parent is published
    with a serializing depends_on on the owner + an edit_on_prior flag, so the
    market serializes the writers and the second EDITS instead of clobbering."""

    def _steps(self) -> list[dict]:
        return [{"step_id": "PM-2-step-2", "target_file": "main.js", "verify": "test -f main.js", "title": "levels"}]

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_owned_file_gets_depends_on_and_edit_flag(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        prior = {"main.js": {"owner_step_id": "PM-1-S4", "owner_parent": "PM-0001-1"}}
        consumer._publish_step_tasks(
            "PM-0001-2",
            {"run_id": "r"},
            self._steps(),
            blueprint_id="b",
            blueprint_path="p",
            prior_file_owners=prior,
        )
        cmd = consumer._svc.publish_work_item.call_args_list[0].args[0]
        assert "PM-1-S4" in cmd.depends_on
        pub_step = cmd.payload["construction_step"]
        assert pub_step["edit_on_prior"] is True
        assert pub_step["edit_on_prior_owner"] == "PM-1-S4"

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_unowned_file_keeps_minimization(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        steps = [{"step_id": "S", "target_file": "style.css", "verify": "test -f style.css", "title": "style"}]
        consumer._publish_step_tasks(
            "PM-0001-2",
            {"run_id": "r"},
            steps,
            blueprint_id="b",
            blueprint_path="p",
            prior_file_owners={"main.js": {"owner_step_id": "PM-1-S4", "owner_parent": "PM-0001-1"}},
        )
        cmd = consumer._svc.publish_work_item.call_args_list[0].args[0]
        assert cmd.depends_on == ()  # no injected dependency for an unowned file
        assert "edit_on_prior" not in cmd.payload["construction_step"]

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    def test_same_parent_owner_not_self_serialized(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock()
        consumer = CEConsumer(workspace="/test", worker_id="w1")
        # The owner belongs to the SAME parent -> not a cross-parent conflict, no injection.
        prior = {"main.js": {"owner_step_id": "PM-2-S1", "owner_parent": "PM-0001-2"}}
        consumer._publish_step_tasks(
            "PM-0001-2",
            {"run_id": "r"},
            self._steps(),
            blueprint_id="b",
            blueprint_path="p",
            prior_file_owners=prior,
        )
        cmd = consumer._svc.publish_work_item.call_args_list[0].args[0]
        assert "PM-2-S1" not in cmd.depends_on
