from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from polaris.cells.roles.kernel.internal.transaction import tool_surface
from polaris.cells.roles.profile.public.service import RoleTurnRequest, profile_from_dict


def _profile() -> Any:
    return profile_from_dict(
        {
            "role_id": "director",
            "display_name": "Director",
            "prompt_policy": {"core_template_id": "director.core"},
            "tool_policy": {"whitelist": ["write_file", "repo_tree"]},
            "data_policy": {"data_subdir": "director"},
        }
    )


def _context_result() -> Any:
    return SimpleNamespace(messages=[], token_estimate=0)


def test_plan_transaction_tool_surface_honors_forced_no_tools(monkeypatch: Any) -> None:
    """A text-only request must not construct provider tool schemas."""

    def _fail_build_native_tool_schemas(_profile: Any) -> list[dict[str, Any]]:
        raise AssertionError("text-only requests must not build native schemas")

    monkeypatch.setattr(tool_surface, "build_native_tool_schemas", _fail_build_native_tool_schemas)
    monkeypatch.setattr(
        tool_surface,
        "_apply_runtime_tool_policy",
        lambda **kwargs: (kwargs["tool_definitions"], {"runtime_tool_policy_applied": True}),
    )

    request = RoleTurnRequest(
        workspace="/tmp/workspace",
        message="[mode:propose] do not call tools",
        context_override={
            "_transaction_kernel_forced_tool_choice": "none",
            "_transaction_kernel_forced_tool_definitions": [],
        },
    )

    plan = tool_surface.plan_transaction_tool_surface(
        role="director",
        profile=_profile(),
        request=request,
        context_result=_context_result(),
        messages=[],
        workspace="/tmp/workspace",
        mode="turn",
    )

    assert plan.tool_definitions == []
    assert plan.tool_choice_override is None
    assert plan.runtime_tool_policy_audit == {"runtime_tool_policy_applied": True}
    assert plan.conflict_error is None


def test_plan_transaction_tool_surface_uses_mode_specific_pin_targets(monkeypatch: Any) -> None:
    """Turn and stream paths keep their historical target pin sources."""

    observed_targets: list[tuple[str, ...]] = []
    native_schema = {"type": "function", "function": {"name": "write_file", "parameters": {}}}

    monkeypatch.setattr(tool_surface, "build_native_tool_schemas", lambda _profile: [native_schema])
    monkeypatch.setattr(
        tool_surface,
        "_apply_runtime_tool_policy",
        lambda **kwargs: (kwargs["tool_definitions"], {"runtime_tool_policy_applied": True}),
    )
    monkeypatch.setattr(tool_surface, "extract_write_tool_pin_target_files", lambda _context: ["turn.py"])
    monkeypatch.setattr(tool_surface, "extract_declared_step_target_files", lambda _context: ["stream.py"])
    monkeypatch.setattr(tool_surface, "resolve_from_scratch_write_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "resolve_repair_edit_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "should_use_weak_director_slim_tool_schema", lambda **_kwargs: False)

    def _pin_tool_file_param_to_targets(
        definitions: list[dict[str, Any]],
        targets: list[str],
    ) -> list[dict[str, Any]]:
        observed_targets.append(tuple(targets))
        return definitions

    monkeypatch.setattr(tool_surface, "pin_write_tool_file_param_to_targets", _pin_tool_file_param_to_targets)

    request = RoleTurnRequest(
        workspace="/tmp/workspace",
        message="implement",
        context_override={"task_id": "TASK-1"},
    )

    for mode in ("turn", "stream"):
        plan = tool_surface.plan_transaction_tool_surface(
            role="director",
            profile=_profile(),
            request=request,
            context_result=_context_result(),
            messages=[],
            workspace="/tmp/workspace",
            mode=mode,
        )
        assert plan.tool_definitions == [native_schema]

    assert observed_targets == [("turn.py",), ("stream.py",)]


def test_plan_transaction_tool_surface_projects_function_tool_choice(monkeypatch: Any) -> None:
    """Function tool-choice override belongs to the tool-surface plan."""
    native_schema = {"type": "function", "function": {"name": "write_file", "parameters": {}}}
    forced_choice = {"type": "function", "function": {"name": "write_file"}}

    monkeypatch.setattr(tool_surface, "build_native_tool_schemas", lambda _profile: [native_schema])
    monkeypatch.setattr(
        tool_surface,
        "_apply_runtime_tool_policy",
        lambda **kwargs: (kwargs["tool_definitions"], {"runtime_tool_policy_applied": True}),
    )
    monkeypatch.setattr(tool_surface, "resolve_from_scratch_write_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "resolve_repair_edit_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "should_use_weak_director_slim_tool_schema", lambda **_kwargs: False)

    request = RoleTurnRequest(
        workspace="/tmp/workspace",
        message="implement",
        context_override={"_transaction_kernel_forced_tool_choice": forced_choice},
    )

    plan = tool_surface.plan_transaction_tool_surface(
        role="director",
        profile=_profile(),
        request=request,
        context_result=_context_result(),
        messages=[],
        workspace="/tmp/workspace",
        mode="turn",
    )

    assert plan.tool_choice_override == forced_choice


def test_plan_transaction_tool_surface_projects_required_tool_choice(monkeypatch: Any) -> None:
    """The provider-native required token is forwarded as a tool-choice override."""
    monkeypatch.setattr(tool_surface, "build_native_tool_schemas", lambda _profile: [])
    monkeypatch.setattr(
        tool_surface,
        "_apply_runtime_tool_policy",
        lambda **kwargs: (kwargs["tool_definitions"], {"runtime_tool_policy_applied": True}),
    )
    monkeypatch.setattr(tool_surface, "resolve_from_scratch_write_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "resolve_repair_edit_target", lambda _context, _workspace: "")
    monkeypatch.setattr(tool_surface, "should_use_weak_director_slim_tool_schema", lambda **_kwargs: False)

    request = RoleTurnRequest(
        workspace="/tmp/workspace",
        message="implement",
        context_override={"_transaction_kernel_forced_tool_choice": "required"},
    )

    plan = tool_surface.plan_transaction_tool_surface(
        role="director",
        profile=_profile(),
        request=request,
        context_result=_context_result(),
        messages=[],
        workspace="/tmp/workspace",
        mode="turn",
    )

    assert plan.tool_choice_override == "required"
