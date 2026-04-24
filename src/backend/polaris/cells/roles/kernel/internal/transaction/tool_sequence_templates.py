"""工具序列模板库 — 为 Task Contract Builder 提供正例序列模板和恢复协议。

根据任务契约特征（required_tools, ordered_tool_groups, min_tool_calls）
识别任务类型，并生成对应的：
1. 正例序列模板（POSITIVE examples）
2. 失败恢复协议（Recovery protocol）

架构位置: TransactionKernel / Task Contract 层
"""

from __future__ import annotations

# 搜索/定位类工具
_SEARCH_DISCOVERY_TOOLS: frozenset[str] = frozenset(
    {
        "glob",
        "repo_rg",
        "repo_glob",
        "grep",
        "search_code",
        "ripgrep",
        "repo_tree",
        "list_directory",
        "file_exists",
    }
)

# 读取类工具
_READ_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "repo_read_head",
        "repo_read_slice",
        "repo_read_tail",
        "repo_read_around",
    }
)

# 编辑/写类工具
_EDIT_WRITE_TOOLS: frozenset[str] = frozenset(
    {
        "edit_blocks",
        "precision_edit",
        "search_replace",
        "edit_file",
        "repo_apply_diff",
        "append_to_file",
        "write_file",
        "create_file",
    }
)


def _group_has_any(group: list[str], candidates: frozenset[str]) -> bool:
    """Check if any tool in group is in candidates."""
    return any(t in candidates for t in group)


def build_sequence_template(
    required_tools: list[str],
    required_any_groups: list[list[str]],
    ordered_tool_groups: list[list[str]],
    min_tool_calls: int,
    requires_write: bool,
    requires_verify: bool,
) -> str:
    """根据契约特征构建正例序列模板文本。

    识别以下任务类型并返回对应模板：
    - search-then-read: 搜索/查找 → 读取
    - edit-then-verify: 编辑 → 验证读取
    - repeat-read: 多次读取同一文件
    - search-replace: 搜索 → 读取确认 → 替换
    """
    lines: list[str] = ["\nPOSITIVE TOOL SEQUENCE TEMPLATES:"]
    templates_added = 0

    # 1. 基于 ordered_tool_groups 识别序列类型
    if ordered_tool_groups:
        # 检查 [search/read] -> [read_file] 模式（非写操作）
        if len(ordered_tool_groups) == 2 and not requires_write:
            first_group = ordered_tool_groups[0]
            second_group = ordered_tool_groups[1]
            if _group_has_any(first_group, _SEARCH_DISCOVERY_TOOLS) and _group_has_any(second_group, _READ_TOOLS):
                lines.append(
                    "TEMPLATE [Search-Then-Read]: "
                    "Step 1: Use glob/repo_rg to locate target files. "
                    "Step 2: Use read_file to read the content of the identified file(s). "
                    "DO NOT stop after Step 1. You MUST complete Step 2 before final text."
                )
                templates_added += 1

        # 检查 [write] -> [read_verify] 模式（编辑后验证）
        if len(ordered_tool_groups) == 2 and requires_write:
            first_group = ordered_tool_groups[0]
            second_group = ordered_tool_groups[1]
            if _group_has_any(first_group, _EDIT_WRITE_TOOLS) and _group_has_any(second_group, _READ_TOOLS):
                lines.append(
                    "TEMPLATE [Edit-Then-Verify]: "
                    "Step 1: read_file the target file to confirm exact content. "
                    "Step 2: Use precision_edit/append_to_file/edit_blocks to make changes. "
                    "Step 3: read_file again to verify the modification succeeded. "
                    "All three steps must be in the SAME batch if the benchmark contract requires single-batch completion."
                )
                templates_added += 1

        # 检查三步序列（搜索 → 读取 → 写）
        if len(ordered_tool_groups) >= 2 and requires_write:
            # 搜索-替换型：通常有搜索 → 写，但中间缺少read_file是常见失败模式
            first_group = ordered_tool_groups[0]
            if _group_has_any(first_group, _SEARCH_DISCOVERY_TOOLS | _READ_TOOLS):
                lines.append(
                    "TEMPLATE [Search-Replace]: "
                    "Step 1: Use repo_rg/ripgrep to locate occurrences. "
                    "Step 2: Use read_file to read the target file content EXACTLY. "
                    "Step 3: Use search_replace or precision_edit to perform replacement. "
                    "CRITICAL: If you mix read tools (repo_rg/read_file) and write tools (search_replace) "
                    "in the SAME parallel batch, the Read-Write Barrier will REJECT it. "
                    "Use ordered groups: [repo_rg/read_file] first, then [search_replace]."
                )
                templates_added += 1

    # 2. 多次读取型（idempotent read）
    if min_tool_calls > 1 and not requires_write and any(t in _READ_TOOLS for t in required_tools):
        lines.append(
            f"TEMPLATE [Repeat-Read]: User explicitly requested {min_tool_calls} read operations. "
            f"You MUST call read_file exactly {min_tool_calls} times. "
            f"Do NOT stop after the first read. Report results after all {min_tool_calls} reads complete."
        )
        templates_added += 1

    # 3. 如果没有匹配特定模板，添加通用指导
    if templates_added == 0 and (requires_write or requires_verify):
        lines.append(
            "TEMPLATE [General-Mutation]: "
            "Step 1: read_file to confirm exact content. "
            "Step 2: Use the safest available write tool (append_to_file > precision_edit > edit_file). "
            "Step 3: read_file again to verify."
        )

    # 4. 通用完整性检查
    lines.append(
        "\nCOMPLETION CHECK: Before finalizing, verify you have satisfied ALL required tool groups. "
        "A batch that only completes partial steps is INVALID and will be scored as FAILURE."
    )

    return "\n".join(lines)


def build_recovery_protocol(
    required_tools: list[str],
    required_any_groups: list[list[str]],
    available_write_tools: list[str],
) -> str:
    """构建失败恢复协议文本。

    为常见失败场景提供标准恢复流程。
    """
    lines: list[str] = ["\nTOOL FAILURE RECOVERY PROTOCOL:"]

    # Edit 工具失败恢复
    has_edit = any(t in _EDIT_WRITE_TOOLS for t in required_tools) or any(
        any(t in _EDIT_WRITE_TOOLS for t in group) for group in required_any_groups
    )

    if has_edit:
        # 构建降级路径字符串
        fallback_order = []
        for t in ("append_to_file", "precision_edit", "edit_file", "search_replace", "write_file"):
            if t in available_write_tools:
                fallback_order.append(t)
        fallback_str = " -> ".join(fallback_order) if fallback_order else "append_to_file"

        lines.append(
            f"1. EDIT FAILURE (no match / search not found): "
            f"→ Immediately call read_file() on the target file. "
            f"→ Copy the EXACT text character-by-character from the file output. "
            f"→ Retry with precision_edit using the verified text. "
            f"→ If precision_edit still fails, downgrade to: {fallback_str}. "
            f"→ NEVER retry edit_blocks with the same incorrect search string."
        )

    # 搜索后未继续的恢复
    has_search = any(t in _SEARCH_DISCOVERY_TOOLS for t in required_tools)
    if has_search:
        lines.append(
            "2. SEARCH-THEN-STALL (glob/repo_rg returned results but you stopped): "
            "→ You have located files but have NOT completed the task. "
            "→ Continue with the next step: read_file the identified files. "
            "→ Stopping after search alone is a FAILURE."
        )

    # 通用恢复规则
    lines.append(
        "3. ANY TOOL FAILURE: Do NOT return plain-text completion after a tool failure. "
        "You MUST attempt recovery using read_file verification or alternative tools."
    )

    lines.append(
        "4. PARTIAL COMPLETION: If you have only executed part of a required sequence, "
        "continue with the next step. Stopping early is a FAILURE."
    )

    return "\n".join(lines)


def extract_expected_read_count(
    required_tools: list[str],
    ordered_tool_groups: list[list[str]],
    min_tool_calls: int,
    requires_write: bool,
) -> int:
    """提取预期的读取次数，用于 Circuit Breaker 豁免。

    当任务明确要求多次读取时（如 idempotent-read），
    Circuit Breaker 不应将合法的重复读取误判为 stagnation。
    """
    # 如果明确要求多次调用且不是写操作，返回 min_tool_calls
    if min_tool_calls > 1 and not requires_write and any(t in _READ_TOOLS for t in required_tools):
        return min_tool_calls

    # 检查 ordered_tool_groups 中是否包含多次 read
    read_count = 0
    for group in ordered_tool_groups:
        if _group_has_any(group, _READ_TOOLS):
            read_count += 1

    # 如果编辑后需要验证读取，也算一次预期读取
    if requires_write:
        # 通常 edit-then-verify 需要至少一次验证读取
        read_count = max(read_count, 1)

    return max(read_count, 1)
