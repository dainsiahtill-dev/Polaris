from pathlib import Path


class TestBlueprintStepsSignal:
    """三层裂变 I2: Director 的施工步骤蓝图智能注入(default-on, must-have)。"""

    def test_injects_for_director_by_default(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            BlueprintStepsSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="director",
            phase="exec",
            task_id="PM-9-S2",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_blueprint_step=lambda: (
                "step PM-9-S2: game.js ≤100行\nsignatures: function gameLoop()\nverify: node --check game.js"
            ),
        )
        signal = BlueprintStepsSignal()
        assert signal.applies_to(ctx) is True
        block = signal.build(ctx)
        assert block is not None
        assert "施工步骤蓝图" in block.content
        assert block.level == "must_have"

    def test_absent_step_injects_nothing(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            BlueprintStepsSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="director",
            phase="exec",
            task_id="t",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
        )
        assert BlueprintStepsSignal().build(ctx) is None

    def test_not_for_other_roles(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            BlueprintStepsSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="pm",
            phase="exec",
            task_id="t",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
        )
        assert BlueprintStepsSignal().applies_to(ctx) is False

    def test_flag_can_disable(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            BlueprintStepsSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="director",
            phase="exec",
            task_id="t",
            policy_flags={"include_blueprint_step": False},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
        )
        assert BlueprintStepsSignal().applies_to(ctx) is False


class TestResidentAgiCapabilitySurfaceSignal:
    """Resident AGI: 与其他角色同底座,但注入平台级能力面与治理边界。"""

    def test_injects_for_resident_agi_by_default(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiCapabilitySurfaceSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="resident_agi",
            phase="supervise",
            task_id="goal-1",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_capabilities=lambda: (
                "runtime_foundation: roles.runtime + ContextOS + TurnEngine\n"
                "capabilities:\n"
                "- contextos.final_request_audit.read"
            ),
        )
        signal = ResidentAgiCapabilitySurfaceSignal()
        assert signal.applies_to(ctx) is True
        block = signal.build(ctx)
        assert block is not None
        assert "Resident AGI 能力面" in block.content
        assert "roles.runtime + ContextOS + TurnEngine" in block.content
        assert block.level == "must_have"

    def test_not_for_other_roles(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiCapabilitySurfaceSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="chief_engineer",
            phase="supervise",
            task_id="goal-1",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_capabilities=lambda: "capabilities",
        )
        assert ResidentAgiCapabilitySurfaceSignal().applies_to(ctx) is False

    def test_flag_can_disable(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiCapabilitySurfaceSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="resident_agi",
            phase="supervise",
            task_id="goal-1",
            policy_flags={"include_resident_agi_capability_surface": False},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_capabilities=lambda: "capabilities",
        )
        assert ResidentAgiCapabilitySurfaceSignal().applies_to(ctx) is False

    def test_default_registry_resolves_capability_surface_for_resident_agi(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            RoleSignalRegistry,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="resident_agi",
            phase="supervise",
            task_id="goal-1",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_capabilities=lambda: "capabilities",
        )
        provider_ids = [provider.id for provider in RoleSignalRegistry().resolve(ctx)]
        assert "resident_agi_capability_surface" in provider_ids

    def test_signal_source_provider_renders_capability_surface(self, tmp_path: Path) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.gateway import (
            ContextGatewayConfig,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.signal_sources import (
            SignalSourceProvider,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
            TokenEstimator,
        )

        class Policy:
            max_context_tokens = 8192

        config = ContextGatewayConfig(
            resident_agi_capability_provider=lambda _workspace: {
                "schema_version": "resident.agi_capability_surface.v1",
                "role_id": "resident_agi",
                "runtime_foundation": "roles.runtime + ContextOS + TurnEngine",
                "implementation_cell": "resident.autonomy",
                "items": [
                    {
                        "capability_id": "contextos.final_request_audit.read",
                        "name": "Final provider-request audit",
                        "category": "llm_audit",
                        "access": "read_only",
                        "purpose": "Verify actual provider request evidence.",
                        "contract_ref": "roles.final_request_context_audit",
                        "risk_level": "low",
                        "guardrails": ["Provider request snapshots are the truth source."],
                        "evidence_refs": ["runtime/contexts/<shard>/<hash>"],
                    }
                ],
            }
        )
        source = SignalSourceProvider(
            workspace=tmp_path,
            config=config,
            policy=Policy(),
            token_estimator=TokenEstimator(),
            trigger_pct_resolver=lambda _override: 0.8,
        )

        rendered = source.get_resident_agi_capabilities()

        assert rendered is not None
        assert "roles.runtime + ContextOS + TurnEngine" in rendered
        assert "non_bypass_rules" in rendered
        assert "PM -> Chief Engineer -> Director" in rendered
        assert "contextos.final_request_audit.read" in rendered


class TestResidentAgiDecisionTraceSignal:
    """Resident AGI 决策交接: CE/Director/QA 消费治理判断,PM 不被反向污染。"""

    def test_injects_for_director_by_default(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiDecisionTraceSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="director",
            phase="exec",
            task_id="TASK-1",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_decision_trace=lambda: (
                "schema_version: resident.agi_decision_trace_signal.v1\n"
                "recent_decisions:\n"
                "- decision-1 | actor=resident | stage=goal_staging | verdict=success"
            ),
        )
        signal = ResidentAgiDecisionTraceSignal()
        assert signal.applies_to(ctx) is True
        block = signal.build(ctx)
        assert block is not None
        assert "Resident AGI 决策交接" in block.content
        assert "resident.agi_decision_trace_signal.v1" in block.content
        assert block.level == "must_have"

    def test_not_for_pm_or_resident_agi(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiDecisionTraceSignal,
            SignalBuildContext,
        )

        for role in ("pm", "resident_agi"):
            ctx = SignalBuildContext(
                role=role,
                phase="exec",
                task_id="TASK-1",
                policy_flags={},
                get_project_structure=lambda: None,
                get_task_history=lambda _tid: None,
                get_resident_agi_decision_trace=lambda: "decision trace",
            )
            assert ResidentAgiDecisionTraceSignal().applies_to(ctx) is False

    def test_flag_can_disable(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            ResidentAgiDecisionTraceSignal,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="chief_engineer",
            phase="blueprint",
            task_id="TASK-1",
            policy_flags={"include_resident_agi_decision_trace": False},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_decision_trace=lambda: "decision trace",
        )
        assert ResidentAgiDecisionTraceSignal().applies_to(ctx) is False

    def test_default_registry_resolves_for_qa(self) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.role_signals import (
            RoleSignalRegistry,
            SignalBuildContext,
        )

        ctx = SignalBuildContext(
            role="qa",
            phase="audit",
            task_id="TASK-1",
            policy_flags={},
            get_project_structure=lambda: None,
            get_task_history=lambda _tid: None,
            get_resident_agi_decision_trace=lambda: "decision trace",
        )
        provider_ids = [provider.id for provider in RoleSignalRegistry().resolve(ctx)]
        assert "resident_agi_decision_trace" in provider_ids

    def test_signal_source_provider_renders_decision_trace(self, tmp_path: Path) -> None:
        from polaris.cells.roles.kernel.internal.context_gateway.gateway import (
            ContextGatewayConfig,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.signal_sources import (
            SignalSourceProvider,
        )
        from polaris.cells.roles.kernel.internal.context_gateway.token_estimator import (
            TokenEstimator,
        )

        class Policy:
            max_context_tokens = 8192

        config = ContextGatewayConfig(
            resident_agi_decision_trace_provider=lambda _workspace: [
                {
                    "decision_id": "decision-1",
                    "actor": "resident",
                    "stage": "goal_staging",
                    "summary": "Promote governed goal through PM bridge.",
                    "verdict": "success",
                    "confidence": 0.86,
                    "selected_option_id": "continue_execution",
                    "goal_id": "goal-1",
                    "strategy_tags": ["goal_governance", "pm_bridge"],
                    "evidence_refs": ["workspace/meta/resident/decision_trace.jsonl"],
                    "context_refs": ["runtime/contexts/abc123"],
                },
                {
                    "decision_id": "decision-2",
                    "actor": "director",
                    "stage": "task_execution",
                    "summary": "Plain Director execution should not be injected.",
                    "verdict": "success",
                },
            ]
        )
        source = SignalSourceProvider(
            workspace=tmp_path,
            config=config,
            policy=Policy(),
            token_estimator=TokenEstimator(),
            trigger_pct_resolver=lambda _override: 0.8,
        )

        rendered = source.get_resident_agi_decision_trace()

        assert rendered is not None
        assert "resident.agi_decision_trace_signal.v1" in rendered
        assert "source_of_truth: workspace/meta/resident/decision_trace.jsonl" in rendered
        assert "decision-1" in rendered
        assert "goal_governance, pm_bridge" in rendered
        assert "decision-2" not in rendered
