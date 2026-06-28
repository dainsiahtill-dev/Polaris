"""F31 (2026-06-16): a from-scratch build Director turn must materialize.

The kernel resolves the delivery contract by text-classifying the turn message;
a weak/terse build goal can fall through to the default ANALYZE_ONLY, whose
delivery-mode-filter then DROPS the Director's write tools ->
director_no_materialized_changes even though the Director DID emit writes
(factory-bench L4-23: 3 write tools dropped in analyze_only mode, 0 files).
``_pin_materialize_delivery_mode`` pins the explicit ``[mode:materialize]``
marker for requires-fresh tasks so the contract is deterministic.
"""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director import execute_method as director_execute_method
from polaris.cells.roles.adapters.internal.director.execute_method import (
    _build_materialization_quality_repair_message,
    _pin_materialize_delivery_mode,
)
from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _quality_repair_existing_target_tool_definitions,
)
from polaris.cells.roles.kernel.public import DeliveryMode
from polaris.cells.roles.kernel.public.transaction_contracts import (
    extract_continuation_prompt_metadata,
    resolve_delivery_mode,
)


class TestPinMaterializeDeliveryMode:
    def test_fresh_create_context_pins_materialize_control_plane(self) -> None:
        context = {"context_os_snapshot": {}, "metadata": {"task_id": "t1"}}
        pin_context = getattr(director_execute_method, "_pin_materialize_context_delivery_mode", None)

        assert pin_context is not None
        assert pin_context(context, True) is context
        assert context["delivery_mode"] == "materialize_changes"
        assert context["metadata"]["delivery_mode"] == "materialize_changes"

    def test_non_fresh_context_is_unchanged(self) -> None:
        context = {"context_os_snapshot": {}, "metadata": {"task_id": "t1"}}
        pin_context = getattr(director_execute_method, "_pin_materialize_context_delivery_mode", None)

        assert pin_context is not None
        assert pin_context(context, False) is context
        assert "delivery_mode" not in context
        assert context["metadata"] == {"task_id": "t1"}

    def test_fresh_create_without_marker_is_pinned(self) -> None:
        out = _pin_materialize_delivery_mode("Create main.py and a package", True)
        assert out.startswith("[mode:materialize]\n")
        assert "Create main.py" in out

    def test_marker_already_present_is_unchanged(self) -> None:
        msg = "[mode:materialize]\nbuild it"
        assert _pin_materialize_delivery_mode(msg, True) == msg

    def test_marker_present_other_case_is_unchanged(self) -> None:
        msg = "[MODE:MATERIALIZE] build it"
        assert _pin_materialize_delivery_mode(msg, True) == msg

    def test_non_fresh_task_is_unchanged(self) -> None:
        # An analysis/non-create Director turn must NOT be forced to materialize.
        msg = "Review the existing code and report issues"
        assert _pin_materialize_delivery_mode(msg, False) == msg

    def test_empty_message_non_fresh_unchanged(self) -> None:
        assert _pin_materialize_delivery_mode("", False) == ""

    def test_closed_loop_pinned_message_resolves_to_materialize(self) -> None:
        # The marker the helper injects MUST be honoured by the kernel's
        # delivery-mode classifier — otherwise the pin is inert. A terse goal
        # that would otherwise default to ANALYZE_ONLY must become MATERIALIZE.
        terse_goal = "scaffold the project"
        # Sanity: the terse goal alone does not already resolve to materialize
        # (else the test proves nothing) — but if the rule engine happens to,
        # the pin is still correct. The load-bearing assertion is the pinned one.
        pinned = _pin_materialize_delivery_mode(terse_goal, True)
        assert resolve_delivery_mode(pinned).mode == DeliveryMode.MATERIALIZE_CHANGES

    def test_quality_repair_message_pins_initial_and_continuation_delivery_mode(self) -> None:
        message = _build_materialization_quality_repair_message(
            original_message="Create TypeScript files.",
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'src/domain/humidity.ts'"
            ],
            changed_files=["package.json", "src/index.ts"],
            missing_target_files=["src/domain/humidity.ts"],
        )

        assert message.startswith("[mode:materialize]\n")
        assert resolve_delivery_mode(message).mode == DeliveryMode.MATERIALIZE_CHANGES
        assert extract_continuation_prompt_metadata(message)["delivery_mode"] == "materialize_changes"

    def test_single_missing_target_quality_repair_compacts_conflicting_original_contract(self) -> None:
        message = _build_materialization_quality_repair_message(
            original_message=(
                "[mode:materialize]\n"
                "PM Task Contract / 任务合同:\n"
                "任务: 实现 迷你行星天气球 Python 包结构与领域模型\n"
                "描述: 创建 requirements.txt、src/__init__.py、src/models/ 与需求派生模型文件。\n"
                "目标: 在工作区根交付 迷你行星天气球 的 Python src/ 包、领域模型和可导入核心源码。\n"
                "范围: requirements.txt, src/__init__.py, src/models/__init__.py, "
                "src/models/mood.py, src/models/weather.py\n"
                "目标文件覆盖硬门禁: 本任务列出的目标文件必须全部由本轮工具写入或编辑。\n"
                "Verification is required by the user. Include an available verification step.\n"
                "确定性检查进入任务验收：py_compile; content_any:planet|weather|cloud|wind\n"
            ),
            artifact_quality_errors=[
                "Artifact quality scan failed: declared target file missing 'src/models/weather.py'"
            ],
            changed_files=["requirements.txt", "src/__init__.py", "src/models/mood.py"],
            missing_target_files=["src/models/weather.py"],
        )

        assert "ORIGINAL TASK CONTEXT (semantic only" in message
        assert "任务: 实现 迷你行星天气球 Python 包结构与领域模型" in message
        assert "需求关键词: planet, weather, cloud, wind" in message
        assert "[director_quality_repair:write_only_single_target]" in message
        assert "- Target path: src/models/weather.py" in message
        assert "Emit exactly one write_file tool call" in message
        assert "Verification is required by the user" not in message
        assert "Include an available verification step" not in message
        assert "目标文件覆盖硬门禁" not in message
        assert "requirements.txt, src/__init__.py" not in message

    def test_single_target_quality_repair_compact_preserves_ce_blueprint_evidence(self) -> None:
        message = _build_materialization_quality_repair_message(
            original_message=(
                "[mode:materialize]\n"
                "PM Task Contract / 任务合同:\n"
                "任务: Build branded TypeScript market models\n"
                "Chief Engineer Blueprint / CE 蓝图交接:\n"
                "- blueprint_id: ce_TASK-7_valid\n"
                "- handoff_ready: yes (handoff_ready)\n"
                "- blueprint target_files: src/index.ts, src/models/Fairy.ts\n"
                "- ce_llm_blueprint: consumed (advisory_only)\n"
                "Verification commands / 验证命令:\n"
                "- npm run build\n"
            ),
            artifact_quality_errors=["src/main.ts(4,27): error TS2345: branded mismatch"],
            changed_files=["src/index.ts", "src/models/Fairy.ts"],
            repair_target_files=["src/main.ts"],
        )

        assert "ORIGINAL TASK CONTEXT (semantic only" in message
        assert "Chief Engineer Blueprint / CE 蓝图交接" in message
        assert "- blueprint_id: ce_TASK-7_valid" in message
        assert "- handoff_ready: yes (handoff_ready)" in message
        assert "- ce_llm_blueprint: consumed (advisory_only)" in message
        assert "- blueprint target_files: src/index.ts, src/models/Fairy.ts" not in message

    def test_non_fresh_terse_goal_not_forced(self) -> None:
        # Without the pin, a pure-analysis phrasing stays out of MATERIALIZE.
        contract = resolve_delivery_mode("analyze the architecture and summarize")
        assert contract.mode != DeliveryMode.MATERIALIZE_CHANGES

    def test_existing_target_quality_repair_exposes_verification_tool(self) -> None:
        tool_definitions = _quality_repair_existing_target_tool_definitions()
        tool_names = {item.get("function", {}).get("name") for item in tool_definitions if isinstance(item, dict)}

        assert {"edit_file", "write_file", "execute_command"} <= tool_names
