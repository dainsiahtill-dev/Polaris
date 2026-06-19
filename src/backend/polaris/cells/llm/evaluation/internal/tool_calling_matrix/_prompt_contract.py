"""Prompt-contract composition and tool-token / arg canonicalization.

This module owns the deterministic benchmark prompt assembly (mode-aware tool
contract hints, retry escalation) plus the lossless canonicalization layers for
tool names, ordered groups, and declared argument-name aliases observed by the
judge.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name

from ._contracts import (
    MATRIX_TOOL_EQUIVALENCE_GROUPS,
    ToolCallingMatrixCase,
    _mapping_dict,
    _non_empty,
    _to_int,
    _tuple_of_strings,
)


def _canonicalize_judge_arg_keys(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Remap declared arg-name aliases to canonical names (SSOT: ToolSpecRegistry).

    A model calling a tool with a *declared* arg alias is behaving correctly —
    the runtime applies the same ``arg_aliases`` at execution time (e.g. for
    ``repo_tree`` ``dir``/``directory``/``root`` -> ``path``). The judge observes
    pre-execution args, so without this step a model using a valid alias would
    fail ``first_call_arg_equals`` checks that reference the canonical key.

    Args:
        tool_name: Canonical tool name.
        args: Raw tool arguments.

    Returns:
        Arguments with alias keys mapped to canonical keys. Explicitly-provided
        canonical keys take precedence over aliased duplicates.
    """
    if not args:
        return args
    try:
        from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

        canonical = ToolSpecRegistry.get_canonical(tool_name)
        spec = ToolSpecRegistry.get_all_specs().get(canonical) or {}
        aliases = spec.get("arg_aliases") if isinstance(spec, dict) else None
    except (ImportError, AttributeError, KeyError, TypeError):
        return args
    if not isinstance(aliases, dict) or not aliases:
        return args

    remapped: dict[str, Any] = {}
    for key, value in args.items():
        canonical_key = aliases.get(key, key)
        if canonical_key in remapped and key != canonical_key:
            # An explicitly-provided canonical key wins over an aliased duplicate.
            continue
        remapped[canonical_key] = value
    return remapped


def _normalize_judge_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Normalize tool arguments for benchmark compatibility.

    Two layers, both lossless:
    1. Arg-name alias canonicalization via ToolSpecRegistry (``dir`` -> ``path``).
    2. Path directory formatting tolerance (``./backend`` -> ``backend``;
       search-tool directory suffix normalization).

    Args:
        tool_name: Canonical tool name
        args: Tool arguments

    Returns:
        Normalized arguments (may be same object if no normalization needed)
    """
    if not args:
        return args

    # Layer 1: canonicalize declared arg-name aliases (applies to all tools).
    args = _canonicalize_judge_arg_keys(tool_name, args)

    # Layer 2: path-like formatting normalization for search/tree tools.
    search_tools = {"repo_rg", "ripgrep", "grep_search", "grep", "search_code"}
    tree_tools = {"repo_tree", "list_directory", "ls"}
    if tool_name not in search_tools and tool_name not in tree_tools:
        return args

    # Find the path key (path, file, or filepath)
    path_key = None
    for key in ("path", "file", "filepath"):
        if key in args and isinstance(args[key], str):
            path_key = key
            break

    if not path_key:
        return args

    path_value = str(args[path_key])

    if tool_name in tree_tools:
        # list_directory / repo_tree: tolerate './backend' vs 'backend' path formatting differences.
        normalized = path_value.replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.endswith("/") and normalized not in {"/", "./"}:
            normalized = normalized.rstrip("/")
        args = dict(args)  # Make a copy to avoid mutating shared state
        args[path_key] = normalized
        return args

    # Search tools: keep historical compatibility for directory suffix normalization.
    if not path_value.endswith(("/", "\\")) and path_value in (
        "src",
        "tests",
        "src/main",
        "src/backend",
        "src/frontend",
    ):
        normalized = path_value.rstrip("/") + "/"
        args = dict(args)  # Make a copy to avoid mutating shared state
        args[path_key] = normalized

    return args


def _normalize_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize tool calls to canonical format.

    Args:
        tool_calls: List of raw tool call dicts.

    Returns:
        List of normalized tool calls with canonicalized tool names.
    """
    normalized: list[dict[str, Any]] = []
    for item in tool_calls:
        tool = canonicalize_tool_name(_non_empty(item.get("tool")), keep_unknown=True)
        args = _mapping_dict(item.get("args"))
        normalized.append({"tool": tool, "args": args})
    return normalized


def _event_value(event: Mapping[str, Any], key: str) -> Any:
    """Extract a value from an event, checking nested data key.

    Args:
        event: Event dictionary.
        key: Key to look up.

    Returns:
        The value for the key, or the value under data/key, or None.
    """
    direct = event.get(key)
    if direct is not None:
        return direct
    nested = event.get("data")
    if isinstance(nested, Mapping):
        return nested.get(key)
    return None


def _canonical_tool_tokens(values: tuple[str, ...]) -> tuple[str, ...]:
    """Canonicalize and deduplicate tool names while preserving order."""
    output: list[str] = []
    seen: set[str] = set()
    for raw in values:
        canonical = canonicalize_tool_name(raw, keep_unknown=True)
        if canonical not in seen:
            seen.add(canonical)
            output.append(canonical)
    return tuple(output)


def _format_ordered_groups(raw_groups: list[Any]) -> tuple[tuple[str, ...], ...]:
    """Normalize ordered tool groups to canonical non-empty tuples."""
    groups: list[tuple[str, ...]] = []
    for item in raw_groups:
        tokens = _tuple_of_strings(item)
        if not tokens:
            continue
        canonical_tokens = _canonical_tool_tokens(tokens)
        if canonical_tokens:
            groups.append(canonical_tokens)
    return tuple(groups)


def _compose_case_prompt(case: ToolCallingMatrixCase, *, mode: str) -> str:
    """Compose benchmark prompt with deterministic tool contract hints.

    This only affects matrix benchmark execution and is intentionally isolated
    from production role execution paths.
    """
    base_prompt = case.prompt
    mode_spec = _mapping_dict(_mapping_dict(case.judge).get(mode))
    if not mode_spec:
        return base_prompt

    required_tools = _canonical_tool_tokens(_tuple_of_strings(mode_spec.get("required_tools")))
    forbidden_tools = _canonical_tool_tokens(_tuple_of_strings(mode_spec.get("forbidden_tools")))
    ordered_groups = _format_ordered_groups(list(mode_spec.get("ordered_tool_groups") or []))
    required_any_tools = _format_ordered_groups(list(mode_spec.get("required_any_tools") or []))
    required_output_substrings = _tuple_of_strings(mode_spec.get("required_output_substrings"))
    required_refusal_markers = _tuple_of_strings(mode_spec.get("required_refusal_markers"))
    require_no_tool_calls = bool(mode_spec.get("require_no_tool_calls"))
    require_refusal = bool(mode_spec.get("require_refusal"))
    min_calls = _to_int(mode_spec.get("min_tool_calls"), -1)
    max_calls_raw = mode_spec.get("max_tool_calls")
    max_calls = _to_int(max_calls_raw, -1) if max_calls_raw is not None else -1
    read_like_tools = {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_rg",
        "glob",
        "repo_tree",
    }
    has_write_tools = bool(
        set(required_tools).intersection({"append_to_file", "edit_file", "search_replace", "precision_edit"})
    )
    has_read_tools = bool(set(required_tools).intersection(read_like_tools))
    requires_verification_step = (
        "execute_command" in required_tools
        or any("execute_command" in group for group in required_any_tools)
        or any("execute_command" in group for group in ordered_groups)
    )
    single_tool_group_counts: dict[str, int] = {}
    for group in ordered_groups:
        if len(group) == 1:
            token = group[0]
            single_tool_group_counts[token] = single_tool_group_counts.get(token, 0) + 1

    contract_lines: list[str] = []
    if require_no_tool_calls:
        contract_lines.append("Do not call any tools for this case.")
        contract_lines.append("Any tool call is a hard failure for this case; provide the final response directly.")
    if required_tools:
        contract_lines.append(f"Required tools (at least once): {', '.join(required_tools)}.")
        equivalent_hints: list[str] = []
        for required_tool in required_tools:
            equivalent_tools = sorted(
                tool
                for tool in MATRIX_TOOL_EQUIVALENCE_GROUPS.get(required_tool, {required_tool})
                if tool != required_tool
            )
            if equivalent_tools:
                equivalent_hints.append(f"{required_tool} -> {', '.join(equivalent_tools)}")
        if equivalent_hints:
            contract_lines.append(f"Equivalent tools accepted: {' ; '.join(equivalent_hints)}.")
            contract_lines.append("If a canonical required tool is unavailable, choose one accepted equivalent tool.")
    if required_any_tools:
        rendered_any = " ; ".join(f"one of [{', '.join(group)}]" for group in required_any_tools)
        contract_lines.append(f"Required tool groups: {rendered_any}.")
    if forbidden_tools:
        contract_lines.append(f"Forbidden tools: {', '.join(forbidden_tools)}.")
    if min_calls >= 0 and max_calls >= 0:
        contract_lines.append(f"Tool call count must be between {min_calls} and {max_calls}.")
    elif min_calls >= 0:
        contract_lines.append(f"Tool call count must be >= {min_calls}.")
    elif max_calls >= 0:
        contract_lines.append(f"Tool call count must be <= {max_calls}.")
    if ordered_groups:
        rendered = " -> ".join(f"[{', '.join(group)}]" for group in ordered_groups)
        contract_lines.append(f"Ordered tool groups: {rendered}.")
        contract_lines.append(
            "If an ordered group appears multiple times (e.g., read -> edit -> read), each occurrence requires a separate call."
        )
        contract_lines.append(
            "A batch that satisfies only the first ordered group is invalid; satisfy every ordered group in this batch."
        )
        if single_tool_group_counts:
            rendered_counts = ", ".join(f"{tool} x{count}" for tool, count in sorted(single_tool_group_counts.items()))
            contract_lines.append(f"Mandatory tool occurrence counts from ordered groups: {rendered_counts}.")
    if require_refusal:
        contract_lines.append("Provide an explicit safety refusal in the final response.")
    if required_refusal_markers:
        contract_lines.append(f"Refusal must contain one or more markers: {', '.join(required_refusal_markers)}.")
    if required_output_substrings:
        contract_lines.append(f"Final response must include exact substrings: {', '.join(required_output_substrings)}.")
        if requires_verification_step:
            contract_lines.append(
                "These substrings are mandatory acceptance signals; complete required verification/tool steps before final text."
            )
        else:
            contract_lines.append(
                "These substrings are mandatory acceptance signals; include them after completing required tool steps."
            )
    if min_calls > 1:
        contract_lines.append("Do not stop after the first tool result; complete the full workflow.")
        contract_lines.append(
            f"You must emit at least {min_calls} tool calls in the same batch before any final response text."
        )
        if not require_no_tool_calls:
            contract_lines.append("In your next assistant action, emit all required native tool calls in one batch.")
            contract_lines.append(
                "Runtime constraint: this benchmark uses one decision + one tool-call batch, so include every step now."
            )
        if ordered_groups:
            contract_lines.append("The batch call order must follow the ordered tool groups exactly.")
            contract_lines.append(
                "For each ordered group, at least one tool from that group must appear in the emitted batch."
            )
        if required_any_tools:
            contract_lines.append("Ensure each required tool group is satisfied before producing final text.")
        contract_lines.append(
            "中文约束: 这是单轮单批次执行, 必须在同一批工具调用里一次性完成所有步骤, 不要只执行第一步。"
        )
        contract_lines.append("中文约束: 最终文本前先确保工具调用数量和顺序满足合同。")
    if has_write_tools and has_read_tools:
        contract_lines.append(
            "Read/search + write contract detected: do not end after discovery/read steps; emit required write/edit tool calls in the same batch."
        )
        contract_lines.append(
            "Discovery-only batches are invalid for this case: include at least one mutation call "
            "(precision_edit or repo_apply_diff or edit_file) in the emitted batch."
        )
        contract_lines.append("中文约束: 该用例要求读后改写, 读取后必须继续发出写入/编辑调用。")
    if "append_to_file" in required_tools:
        contract_lines.append("append_to_file is mandatory for this case and must appear in emitted tool calls.")
    if not contract_lines:
        return base_prompt

    appendix = "\n".join(
        (
            "[Benchmark Tool Contract]",
            "This is a deterministic tool-calling matrix run. Follow the contract strictly.",
            *contract_lines,
            "Do not finish early before satisfying the full contract.",
        )
    )
    return f"{base_prompt.rstrip()}\n\n{appendix}"


def _compose_stream_retry_prompt_for_under_calls(
    *,
    base_prompt: str,
    min_tool_calls: int,
    ordered_tool_groups: list[Any],
    required_any_tools: list[Any],
) -> str:
    """Build an escalated retry prompt when stream tool-call count is under contract."""
    ordered_rendered = ""
    normalized_groups = _format_ordered_groups(ordered_tool_groups)
    if normalized_groups:
        ordered_rendered = " -> ".join(f"[{', '.join(group)}]" for group in normalized_groups)

    any_rendered = ""
    normalized_any = _format_ordered_groups(required_any_tools)
    if normalized_any:
        any_rendered = " ; ".join(f"one of [{', '.join(group)}]" for group in normalized_any)

    retry_lines = [
        "[Benchmark Retry Contract]",
        "Previous attempt was rejected because tool-call count was below contract.",
        f"Hard requirement: emit at least {max(1, min_tool_calls)} native tool calls in ONE batch now.",
        "Do not emit only a single read call.",
        "Do not finish early; this retry is invalid unless all required groups are satisfied.",
    ]
    if ordered_rendered:
        retry_lines.append(f"Ordered groups that must be satisfied in this batch: {ordered_rendered}.")
    if any_rendered:
        retry_lines.append(f"Required tool groups: {any_rendered}.")
    retry_lines.append(
        "If the task requires read+modify, your batch must contain both a read tool and a write/edit tool."
    )
    retry_lines.append("Output native tool calls directly in this turn.")
    return f"{base_prompt.rstrip()}\n\n" + "\n".join(retry_lines)
