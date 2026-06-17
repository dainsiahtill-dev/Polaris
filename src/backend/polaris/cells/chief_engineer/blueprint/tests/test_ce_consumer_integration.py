"""Integration tests for CEConsumer with ADRStore-backed task-market handoff."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer


class TestCEConsumerTaskMarketHandoffIntegration:
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_ce_consumer_acks_with_task_market_deferred_assignment(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-42"
        claim_result.lease_token = "lease-abc"
        claim_result.payload = {"title": "Test task", "scope_paths": ["/src/main.py"]}

        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        mock_run_preflight.return_value = {
            "blueprint_id": "bp-task-42",
            "guardrails": ["rule1"],
            "no_touch_zones": ["zone1"],
        }

        mock_adr_store = MagicMock()
        mock_adr_store_cls.return_value = mock_adr_store

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-42"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        mock_adr_store.create_blueprint.assert_called_once()
        create_call_args = mock_adr_store.create_blueprint.call_args
        assert create_call_args[0][0] == "bp-task-42"

        mock_adr_store.compile.assert_called_once_with("bp-task-42")

        ack_call_args = mock_svc.acknowledge_task_stage.call_args
        assert ack_call_args is not None
        cmd = ack_call_args[0][0]
        assert cmd.metadata.get("director_pool_assignment") == "deferred_to_task_market"
        assert cmd.metadata.get("blueprint_id") == "bp-task-42"
        assert cmd.metadata.get("blueprint_path") == "runtime/blueprints/bp-task-42.json"
        assert cmd.metadata.get("runtime_blueprint_path") == "runtime/blueprints/bp-task-42.json"
        assert cmd.metadata.get("route") == "chief_blueprint_required"
        assert cmd.metadata.get("blueprint_required") is True
        assert cmd.metadata.get("target_files") == ["/src/main.py"]

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_ce_consumer_ack_does_not_submit_director_workflow(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
    ) -> None:
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "task-99"
        claim_result.lease_token = "lease-xyz"
        claim_result.payload = {"title": "Conflicting task", "scope_paths": ["/src/main.py"]}

        no_claim_result = MagicMock()
        no_claim_result.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim_result]

        ack_result = MagicMock()
        ack_result.ok = True
        ack_result.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack_result

        mock_run_preflight.return_value = {
            "blueprint_id": "bp-task-99",
            "guardrails": [],
            "no_touch_zones": [],
        }

        mock_adr_store = MagicMock()
        mock_adr_store_cls.return_value = mock_adr_store

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        assert len(results) == 1
        assert results[0]["task_id"] == "task-99"
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"

        mock_adr_store.create_blueprint.assert_called_once()
        mock_adr_store.compile.assert_called_once_with("bp-task-99")
        mock_svc.fail_task_stage.assert_not_called()
        mock_svc.acknowledge_task_stage.assert_called_once()


class TestCEConsumerHandoffGate:
    """Tier-2: the Director-handoff quality gate is advisory by default and
    only requeues when KERNELONE_CE_HANDOFF_ENFORCEMENT is opted in."""

    @staticmethod
    def _wire_blocked_claim(mock_get_svc: MagicMock) -> MagicMock:
        # A payload with no acceptance_criteria -> the quality gate blocks.
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim = MagicMock()
        claim.ok = True
        claim.task_id = "task-blk"
        claim.lease_token = "lease-blk"
        claim.payload = {"title": "blocked task", "scope_paths": ["/src/main.py"]}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim, no_claim]
        ack = MagicMock()
        ack.ok = True
        ack.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack
        return mock_svc

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_flag_off_is_advisory_still_acks(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
        monkeypatch,
    ) -> None:
        monkeypatch.delenv("KERNELONE_CE_HANDOFF_ENFORCEMENT", raising=False)
        mock_svc = self._wire_blocked_claim(mock_get_svc)
        mock_run_preflight.return_value = {"blueprint_id": "bp-blk"}
        mock_adr_store_cls.return_value = MagicMock()

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        # Advisory: the task still advances to the Director...
        assert results[0]["ok"] is True
        assert results[0]["status"] == "pending_exec"
        mock_svc.fail_task_stage.assert_not_called()
        mock_svc.acknowledge_task_stage.assert_called_once()
        # ...but the (blocking) decision is surfaced on the ack metadata.
        meta = mock_svc.acknowledge_task_stage.call_args[0][0].metadata
        assert meta["handoff_decision"]["allowed"] is False
        assert meta["handoff_decision"]["blocker_count"] >= 1

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_flag_on_requeues_blocked_handoff(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
        monkeypatch,
    ) -> None:
        monkeypatch.setenv("KERNELONE_CE_HANDOFF_ENFORCEMENT", "1")
        mock_svc = self._wire_blocked_claim(mock_get_svc)
        mock_run_preflight.return_value = {"blueprint_id": "bp-blk"}
        mock_adr_store_cls.return_value = MagicMock()

        consumer = CEConsumer(workspace="/test", worker_id="w1")
        results = consumer.poll_once()

        # Enforced: the blocked task is requeued, never handed to the Director.
        assert results[0]["ok"] is False
        assert results[0]["reason"] == "CE_quality_gate_blocked"
        mock_svc.acknowledge_task_stage.assert_not_called()
        fail_cmd = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_cmd.error_code == "CE_quality_gate_blocked"
        assert fail_cmd.requeue_stage == "pending_design"


class TestCEConsumerStepFission:
    """Three-tier E1: CE fissions a PM task into leaf step tasks on the market."""

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_fission_publishes_leaf_steps_and_marks_parent(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setenv("KERNELONE_CE_STEP_FISSION", "1")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc

        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-9"
        claim_result.lease_token = "lease-9"
        claim_result.payload = {
            "title": "实现打砖块",
            "workspace": str(tmp_path),
            "run_id": "run-1",
            "target_files": ["index.html", "game.js"],
        }
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        ack = MagicMock()
        ack.ok = True
        ack.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack
        mock_run_preflight.return_value = {"blueprint_id": "bp-PM-9"}
        mock_adr_store_cls.return_value = MagicMock()

        steps = [
            {
                "step_id": "PM-9-S1",
                "parent_pm_task": "PM-9",
                "target_file": "index.html",
                "est_lines": 30,
                "signatures": ["<canvas id=game>"],
                "interface_names": ["#game"],
                "verify": "verify ./index.html exists",
                "depends_on": [],
                "title": "结构壳",
            },
            {
                "step_id": "PM-9-S2",
                "parent_pm_task": "PM-9",
                "target_file": "game.js",
                "est_lines": 100,
                "signatures": ["function gameLoop()"],
                "interface_names": ["#game"],
                "verify": "node --check game.js",
                "depends_on": ["PM-9-S1"],
                "title": "游戏循环",
            },
        ]
        consumer = CEConsumer(workspace=str(tmp_path), worker_id="w1")
        with patch.object(CEConsumer, "_run_step_fission", return_value=(steps, [])):
            results = consumer.poll_once()

        assert results[0]["ok"] is True
        assert results[0]["fission_step_count"] == 2
        assert mock_svc.publish_work_item.call_count == 2
        first_cmd = mock_svc.publish_work_item.call_args_list[0][0][0]
        assert first_cmd.stage == "pending_exec"
        assert first_cmd.parent_task_id == "PM-9"
        assert first_cmd.is_leaf is True
        second_cmd = mock_svc.publish_work_item.call_args_list[1][0][0]
        assert second_cmd.depends_on == ("PM-9-S1",)
        ack_meta = mock_svc.acknowledge_task_stage.call_args[0][0].metadata
        assert ack_meta["is_leaf"] is False
        assert ack_meta["fission_step_count"] == 2

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_gate_failure_circuit_breaks_to_requeue(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.setenv("KERNELONE_CE_STEP_FISSION", "1")
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-9"
        claim_result.lease_token = "lease-9"
        claim_result.payload = {"title": "t", "workspace": str(tmp_path)}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        mock_run_preflight.return_value = {"blueprint_id": "bp-PM-9"}
        mock_adr_store_cls.return_value = MagicMock()

        consumer = CEConsumer(workspace=str(tmp_path), worker_id="w1")
        with patch.object(CEConsumer, "_run_step_fission", return_value=([], ["PM-9: no steps"])):
            results = consumer.poll_once()

        assert results[0]["ok"] is False
        assert results[0]["reason"] == "CE_step_gate_failed"
        mock_svc.publish_work_item.assert_not_called()
        fail_cmd = mock_svc.fail_task_stage.call_args[0][0]
        assert fail_cmd.error_code == "CE_step_gate_failed"
        assert fail_cmd.requeue_stage == "pending_design"

    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.run_pre_dispatch_chief_engineer_ctx")
    @patch("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.ADRStore")
    def test_flag_off_keeps_legacy_advance(
        self,
        mock_adr_store_cls: MagicMock,
        mock_run_preflight: MagicMock,
        mock_get_svc: MagicMock,
        monkeypatch,
        tmp_path,
    ) -> None:
        monkeypatch.delenv("KERNELONE_CE_STEP_FISSION", raising=False)
        mock_svc = MagicMock()
        mock_get_svc.return_value = mock_svc
        claim_result = MagicMock()
        claim_result.ok = True
        claim_result.task_id = "PM-9"
        claim_result.lease_token = "lease-9"
        claim_result.payload = {"title": "t", "workspace": str(tmp_path)}
        no_claim = MagicMock()
        no_claim.ok = False
        mock_svc.claim_work_item.side_effect = [claim_result, no_claim]
        ack = MagicMock()
        ack.ok = True
        ack.status = "pending_exec"
        mock_svc.acknowledge_task_stage.return_value = ack
        mock_run_preflight.return_value = {"blueprint_id": "bp-PM-9"}
        mock_adr_store_cls.return_value = MagicMock()

        consumer = CEConsumer(workspace=str(tmp_path), worker_id="w1")
        results = consumer.poll_once()

        assert results[0]["ok"] is True
        assert results[0]["fission_step_count"] == 0
        mock_svc.publish_work_item.assert_not_called()


class TestFissionExtraction:
    """I3-r6: cloud output shape drifts (think wrappers, fences, prose)."""

    @staticmethod
    def _patch_role_runtime(monkeypatch, payload_text: str) -> None:
        import polaris.cells.roles.runtime.public.service as runtime_service

        class FakeResult:
            ok = True
            output = payload_text
            thinking = None
            role = "chief_engineer"
            metadata: dict[str, object] = {}
            usage: dict[str, object] = {}
            tool_calls: list[object] = []
            artifacts: list[object] = []
            error_message = ""
            error_code = ""

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command):
                return FakeResult()

        monkeypatch.setattr(runtime_service, "RoleRuntimeService", FakeRoleRuntimeService)

    def test_fission_marks_headless_cognitive_blockers_auto_accepted(self, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch

        import polaris.cells.roles.runtime.public.service as runtime_service

        captured_commands = []
        payload_text = (
            '{"construction_steps": [{"step_id": "S1", "target_file": "game.js", '
            '"est_lines": 80, "signatures": ["function loop()"], "verify": "node --check game.js"}]}'
        )

        class FakeResult:
            ok = True
            output = payload_text
            thinking = None
            role = "chief_engineer"
            metadata: dict[str, object] = {}
            usage: dict[str, object] = {}
            tool_calls: list[object] = []
            artifacts: list[object] = []
            error_message = ""
            error_code = ""

        class FakeRoleRuntimeService:
            async def execute_role_session(self, command):
                captured_commands.append(command)
                return FakeResult()

        monkeypatch.setattr(runtime_service, "RoleRuntimeService", FakeRoleRuntimeService)
        with patch(
            "polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service",
            return_value=MagicMock(),
        ):
            consumer = CEConsumer(workspace="/tmp", worker_id="w1")
            steps, errors = consumer._run_step_fission("PM-9", {"title": "t"}, blueprint_id="bp")

        assert errors == []
        assert steps[0]["step_id"] == "PM-9-S1"
        assert len(captured_commands) == 1
        command = captured_commands[0]
        assert command.context["cognitive_runtime_approval_mode"] == "auto_accept"
        assert command.context["cognitive_runtime_approval_scope"] == "chief_engineer_step_fission_preflight"
        assert command.metadata["cognitive_runtime_approval_mode"] == "auto_accept"
        assert command.metadata["cognitive_runtime_approval"] == {
            "mode": "auto_accept",
            "source": "factory_bench_headless_ce_fission",
            "scope": "chief_engineer_step_fission_preflight",
            "approved_by": "ce_consumer",
        }

    def test_think_wrapped_fenced_json(self, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch as _patch

        payload_text = (
            "<think>让我想想拆分方案...</think>\n"
            "好的，以下是拆分:\n```json\n"
            '{"construction_steps": [{"step_id": "S1", "target_file": "game.js", '
            '"est_lines": 80, "signatures": ["function loop()"], "verify": "node --check game.js"}]}\n'
            "```\n完毕。"
        )

        with _patch(
            "polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service",
            return_value=MagicMock(),
        ):
            consumer = CEConsumer(workspace="/tmp", worker_id="w1")
        self._patch_role_runtime(monkeypatch, payload_text)
        steps, errors = consumer._run_step_fission("PM-9", {"title": "t"}, blueprint_id="bp")
        assert errors == []
        assert steps[0]["step_id"] == "PM-9-S1"
        assert steps[0]["target_file"] == "game.js"

    def test_prose_with_inline_object(self, monkeypatch) -> None:
        from unittest.mock import MagicMock, patch as _patch

        payload_text = (
            'Note {"irrelevant": 1} first. Real answer: '
            '{"construction_steps": [{"step_id": "S1", "target_file": "readme.md", '
            '"est_lines": 20, "verify": "verify ./readme.md exists"}]}'
        )

        with _patch(
            "polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service",
            return_value=MagicMock(),
        ):
            consumer = CEConsumer(workspace="/tmp", worker_id="w1")
        self._patch_role_runtime(monkeypatch, payload_text)
        steps, errors = consumer._run_step_fission("PM-9", {"title": "t"}, blueprint_id="bp")
        assert errors == []
        assert steps[0]["step_id"] == "PM-9-S1"
