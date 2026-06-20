"""Blueprint construction-step card rendering for :class:`RoleContextGateway`.

Extracted (behavior-preserving) from ``gateway.py`` during the G8 god-class
decomposition (blueprint REMAINING_04_gateway-py.md, step 4). The gateway keeps
a static delegating shim ``RoleContextGateway._get_blueprint_step`` so the
existing test reach-ins are unaffected; the embedded construction-protocol prompt
strings are moved VERBATIM (no edits — flagged Sec.8 governance follow-up only).

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from typing import Any


def build_blueprint_step_card(request: Any) -> str | None:
    """施工步骤卡（三层裂变 I2）：从请求上下文读取 construction_step。

    有界注入：签名骨架 + 接口定名 + verify 判据 + 行数预算。严禁全文
    （16k 窗口实证 W1.5c 后可用提示仅 ~5-6k tokens）。数据由 director
    消费路径放入 context_override（市场 leaf 步任务的 payload 字段）。
    """
    context_override = getattr(request, "context_override", None)
    if not isinstance(context_override, dict):
        return None
    step = context_override.get("construction_step")
    if not isinstance(step, dict):
        return None
    lines: list[str] = []
    step_id = str(step.get("step_id") or "").strip()
    target_file = str(step.get("target_file") or "").strip()
    if step_id or target_file:
        est = step.get("est_lines")
        lines.append(f"step {step_id}: {target_file}" + (f" (≤{est}行)" if est else ""))
    signatures = [str(s).strip() for s in (step.get("signatures") or []) if str(s).strip()]
    if signatures:
        lines.append("signatures: " + "; ".join(signatures[:12]))
    # Skeleton step (I3-r30): without this the weak model tries to fully implement
    # every signature in one write and truncates (finish_reason=length). Force
    # minimal empty stubs only — the fill steps implement the bodies later.
    if step.get("skeleton_stub_only"):
        lines.append(
            "[骨架步·只写空桩] 本步只为上述每个签名写一个最小空函数体"
            "（JS: `function 名(){}`；Python: `def 名(): pass`），"
            "严禁实现任何逻辑、严禁填写函数体内容——逻辑由后续填充步逐个补。"
            "一次 write_file 写完全部空桩即可，务必极简以一轮落盘"
            "（试图实现整套逻辑会超出输出预算被截断、导致本步零落盘失败）。"
        )
        # P2 (deterministic file-assembly protocol): the skeleton is the interface
        # LAW. It must emit the COMPLETE file shell + one anchor marker per body so
        # the fill steps are scoped patches a merger applies (P3) — not whole-file
        # rewrites the weak model re-derives from memory every turn.
        if step.get("file_shell_required"):
            anchor_ids = [str(a).strip() for a in (step.get("anchor_ids") or []) if str(a).strip()]
            lines.append(
                "[骨架=接口法律] 你的输出就是这个文件的接口契约，后续填充步只能在你定的锚点里填实现。必须包含："
                "①全部 import/require 与 export；②全局状态/常量/配置对象；③（前端）DOM 容器与事件绑定接口、元素 id；"
                "④上面每个签名的最小空桩。并在每个函数体处放一个锚点标记："
                "JS/TS: `// @anchor:函数名` 单独成行写在空函数体内；Python: `# @anchor:函数名`。"
                + (f"必须为这些锚点各放一个标记：{', '.join(anchor_ids)}。" if anchor_ids else "")
                + "只写外壳+空桩+锚点,不实现任何逻辑。"
            )
    # Fill step (I3-r31): without this the weak model tries to implement the WHOLE
    # file at once (truncates) or stuffs prose into edit_blocks. Force a bounded,
    # code-only, anchored edit of ONLY this fill's assigned functions.
    if step.get("fill_scope_only"):
        lines.append(
            "[填充步·只实现被分配的函数] 本步只实现上面 signatures 列出的这几个函数/方法的函数体，"
            "其它桩一律别动、也别实现。做法：先 read_file 看到这几个函数当前的空桩原文，"
            "再用 edit_blocks 对每个函数各做一次 SEARCH/REPLACE。"
            # P2.1 (codex 2026-06-15): the new assembly protocol is anchor-scoped, so
            # the REPLACE must NOT be a free whole-function swap that drops the @anchor
            # or alters the signature — that is exactly what the P3 merger rejects.
            "优先只替换锚点函数体内部的实现；"
            "若替换整个函数，函数签名必须逐字不变、`@anchor:` 标记必须原样保留。"
            "edit_blocks 的 blocks/replace 参数里只放纯代码——严禁任何说明/计划/意图文字"
            "（例如 'replace lines 1-46 with full implementation' 是错的，会被工具拒收）。"
            "严禁 write_file 整文件重写、严禁改动 import/export/公共常量/DOM id/事件绑定、"
            "严禁实现未分配的函数（一次实现整个文件会超预算截断、零落盘）。"
        )
        # P2 (deterministic file-assembly protocol): name the exact anchors this fill
        # owns and make the skeleton's interface inviolable. A fill that changes any
        # import/export/signature/public-const/DOM-id is rejected by the merger (P3)
        # and re-asked — never a silent dead-letter.
        anchor_ids = [str(a).strip() for a in (step.get("anchor_ids") or []) if str(a).strip()]
        if anchor_ids:
            lines.append(
                f"[填充锚点] 你只负责这些锚点的函数体：{', '.join(anchor_ids)}。"
                "骨架定下的接口是法律：严禁新增/删除/改名任何 import/export/函数签名/公共常量/DOM id/事件绑定，"
                "严禁移动或删除任何 `@anchor:` 标记。只在你负责的锚点对应空桩处替换函数体，其余原样保留。"
            )
    interfaces = [str(s).strip() for s in (step.get("interface_names") or []) if str(s).strip()]
    if interfaces:
        lines.append("interfaces: " + ", ".join(interfaces[:16]))
    # Interface coherence (I3-r28): the frozen identifiers OTHER files already
    # exposed. The weak Director must reuse these exact names so cross-file refs
    # resolve at runtime (live: main.js must call the id index.html froze, not
    # invent its own). Bounded to a few files/names to respect the 16k window.
    consumed = context_override.get("consumed_interfaces")
    if isinstance(consumed, dict) and consumed:
        consumed_lines: list[str] = []
        for other_target in sorted(consumed)[:6]:
            entry = consumed.get(other_target)
            if not isinstance(entry, dict):
                continue
            names = [str(n).strip() for n in (entry.get("identifiers") or []) if str(n).strip()][:10]
            if names:
                consumed_lines.append(f"  {other_target} 已公开: {', '.join(names)}")
        if consumed_lines:
            lines.append("跨文件接口(必须复用完全相同的名字，勿自创):")
            lines.extend(consumed_lines)
    verify = str(step.get("verify") or "").strip()
    if verify:
        lines.append(f"verify: {verify}")
    depends = [str(s).strip() for s in (step.get("depends_on") or []) if str(s).strip()]
    if depends:
        lines.append("depends_on(已完成): " + ", ".join(depends[:8]))
    # Fix-13 缺陷清单（punch list）：改建式步骤的现状勘察。没有它，
    # 弱执行者读到看似完整的目标文件会判定"已完成"拒绝动笔
    # （live I3-r13: 编辑模式 0/5，三次重试全零 diff）。
    pre_state = context_override.get("pre_state_verify")
    if isinstance(pre_state, dict):
        failing = [str(c).strip() for c in (pre_state.get("failing_clauses") or []) if str(c).strip()]
        total = pre_state.get("total_clauses")
        if pre_state.get("exit_code") == 0:
            lines.append(
                "现状勘察: 验收判据当前已通过（可能由前置步骤满足）。仍须按本步合同产生实际改进；不产生任何文件变更将被拒收。"
            )
        elif failing:
            lines.append(
                f"现状勘察(缺陷清单): 目标文件当前未通过验收，缺 {len(failing)}/{total} 项。你的任务就是补齐下列各项:"
            )
            for index, clause in enumerate(failing[:8], 1):
                lines.append(f"  缺{index}: {clause[:160]}")
            lines.append("文件已存在不等于任务完成；必须实际修改文件使上述各项全部通过。")
        else:
            lines.append("现状勘察: 验收判据当前未通过。必须实际修改目标文件使 verify 通过；不产生变更将被拒收。")
    failure = context_override.get("last_failure")
    if isinstance(failure, dict):
        failure_message = str(failure.get("error_message") or "").strip()
        failure_code = str(failure.get("error_code") or "").strip()
        if failure_message or failure_code:
            lines.append(f"上次尝试失败({failure_code}): {failure_message[:240]}")
            # R7-B (I3-r28, Self-Refine): the prose "don't rewrite" hint empirically
            # fails — the weak model rewrites the file smaller anyway. Replace it with
            # an imperative, format-specific directive that names the anchored edit verb
            # and warns about the deterministic shrink gate (R7-C) that will reject a
            # degraded rewrite.
            lines.append(
                "[修复轮·只做定点编辑] 目标文件已存在且是可用代码，只因上述原因失败。"
                "只修这一处：用 edit_blocks 发 SEARCH/REPLACE 块（或 file+start+end+replace 行区间编辑），"
                "严禁用 write_file 整文件重写。保留所有既有函数/类/逻辑，不得缩短或删除既有内容——"
                "任何使文件明显变小或丢失既有功能的回应都会被自动拒收并退回重做。"
            )
    return "\n".join(lines) or None
