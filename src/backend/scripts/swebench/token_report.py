#!/usr/bin/env python3
"""Build the SWE-bench cost-asymmetry report + merged predictions set.

Reads the per-instance token ledger (written by polaris_solve_one.py) plus the
original and expansion prediction files, then:

  1. Merges predictions -> predictions_expanded.jsonl (union by instance_id;
     a later file wins, e.g. a retried instance with a non-empty patch).
  2. Emits a Markdown cost report: per-instance local (gemma, estimated) vs cloud
     (Kimi, authoritative API usage) tokens, totals, and the asymmetry ratio.

Local tokens run on a self-hosted GPU (≈0 marginal $); cloud tokens are the only
paid path. Cloud $ is an ILLUSTRATIVE estimate using the rates below — adjust to
your contract; the token counts themselves are exact.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

# Illustrative mid-cloud rates ($ per 1M tokens). Token counts are exact; $ is an estimate.
CLOUD_IN_RATE = 0.60
CLOUD_OUT_RATE = 2.50


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def merge_predictions(sources: list[Path], out_path: Path) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for src in sources:
        for rec in read_jsonl(src):
            iid = str(rec.get("instance_id"))
            if not iid:
                continue
            # later source wins, but never overwrite a non-empty patch with an empty one
            prev = merged.get(iid)
            new_has = bool((rec.get("model_patch") or "").strip())
            prev_has = bool((prev or {}).get("model_patch", "").strip()) if prev else False
            if prev is None or new_has or not prev_has:
                merged[iid] = rec
    rows = list(merged.values())
    with open(out_path, "w", encoding="utf-8") as fh:
        for rec in rows:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work-dir", default=os.path.expanduser("~/Temp/swebench-work"))
    ap.add_argument("--ledger", default=None)
    ap.add_argument("--out-md", default=None)
    ap.add_argument("--out-predictions", default=None)
    args = ap.parse_args()

    work = Path(args.work_dir)
    ledger_path = Path(args.ledger) if args.ledger else work / "token_ledger.jsonl"
    out_md = Path(args.out_md) if args.out_md else work / "TOKEN_COST_REPORT_20260606.md"
    out_pred = Path(args.out_predictions) if args.out_predictions else work / "predictions_expanded.jsonl"

    pred_sources = [work / "predictions_batch.jsonl", work / "predictions_new.jsonl"]
    merged = merge_predictions([p for p in pred_sources if p.is_file()], out_pred)
    non_empty = [r for r in merged if (r.get("model_patch") or "").strip()]

    ledger = read_jsonl(ledger_path)

    tot_li = sum(int(r.get("local_in_est", 0)) for r in ledger)
    tot_lo = sum(int(r.get("local_out_est", 0)) for r in ledger)
    tot_ci = sum(int(r.get("cloud_in", 0)) for r in ledger)
    tot_co = sum(int(r.get("cloud_out", 0)) for r in ledger)
    cloud_cost = tot_ci / 1_000_000 * CLOUD_IN_RATE + tot_co / 1_000_000 * CLOUD_OUT_RATE
    n = max(1, len(ledger))
    handler = sum(1 for r in ledger if r.get("apply_path") == "handler")
    fallback = sum(1 for r in ledger if r.get("apply_path") == "fallback")

    lines: list[str] = []
    lines.append("# SWE-bench 成本不对称红利报告 (Cost-Asymmetry Dividend)")
    lines.append("")
    lines.append(f"- 预测集 (predictions_expanded.jsonl): **{len(merged)}** 实例，其中非空补丁 **{len(non_empty)}**")
    lines.append(f"- Token 账本 (token_ledger.jsonl): **{len(ledger)}** 条")
    lines.append(
        f"- 落盘路径: 官方 handler **{handler}** / fallback **{fallback}**（Task 2 修复后官方 handler 可直接落盘）"
    )
    lines.append("")
    lines.append("## 1. 汇总 (Totals)")
    lines.append("")
    lines.append("| 通道 | 输入 tokens | 输出 tokens | 边际成本 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| 本地 gemma-4-26b (自托管, 估算) | {tot_li:,} | {tot_lo:,} | ≈ $0 (自托管 GPU) |")
    lines.append(f"| 云端 Kimi (API, 实测) | {tot_ci:,} | {tot_co:,} | ≈ ${cloud_cost:.4f} (示例费率) |")
    lines.append("")
    lines.append(f"- 每实例平均：本地 in/out ≈ {tot_li // n}/{tot_lo // n}；云端 in/out ≈ {tot_ci // n}/{tot_co // n}")
    lines.append(
        f"- 示例费率：云端 ${CLOUD_IN_RATE}/M in, ${CLOUD_OUT_RATE}/M out（token 数为实测精确值，$ 为示例估算）"
    )
    lines.append(
        f"- **不对称红利**：每道题的全部精修只需约 ${cloud_cost / n:.4f} 云端成本；"
        "定位/分析（高频脏活）由本地 gemma 承担，边际成本≈0。"
    )
    lines.append("")
    lines.append("## 2. 逐题明细 (Per-instance)")
    lines.append("")
    lines.append("| instance | target | apply | 本地 in/out (est) | 云端 in/out (real) |")
    lines.append("|---|---|---|---|---|")
    for r in ledger:
        lines.append(
            f"| {r.get('instance_id')} | {r.get('target', '')} | {r.get('apply_path', '')} "
            f"| {r.get('local_in_est', 0)}/{r.get('local_out_est', 0)} "
            f"| {r.get('cloud_in', 0)}/{r.get('cloud_out', 0)} |"
        )
    lines.append("")
    lines.append("## 3. 解读 (Reading)")
    lines.append("")
    lines.append("- 云端 **输入** tokens 偏大，因为精修阶段把目标文件完整内容喂给 Kimi 以保证 SEARCH 逐字匹配。")
    lines.append(
        "- 这是最直接的进一步降本点：让本地 gemma 先抽取 bug 邻域（±N 行），云端只见最小上下文 → 云端 in 可降一个数量级。"
    )
    lines.append("- 本地承担定位（大上下文、可多轮），云端只做一次小输出的精确编辑——即“本地大拉网 + 云端狙击手”。")

    text = "\n".join(lines) + "\n"
    with open(out_md, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(text)
    print(f"[token_report] predictions_expanded -> {out_pred} ({len(merged)} instances, {len(non_empty)} non-empty)")
    print(f"[token_report] report -> {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
