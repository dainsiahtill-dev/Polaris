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
