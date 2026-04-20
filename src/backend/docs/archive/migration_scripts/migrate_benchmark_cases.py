#!/usr/bin/env python3
"""Case JSON 迁移脚本：将旧格式 AgenticBenchmarkCase 迁移到 UnifiedBenchmarkCase

用法:
    python -m scripts.migrate_benchmark_cases --dry-run
    python -m scripts.migrate_benchmark_cases --execute
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

# 路径
CASES_ROOT = (
    Path(__file__).resolve().parents[1]
    / "polaris"
    / "cells"
    / "llm"
    / "evaluation"
    / "fixtures"
    / "agentic_benchmark"
    / "cases"
)
OUTPUT_ROOT = (
    Path(__file__).resolve().parents[1]
    / "polaris"
    / "kernelone"
    / "benchmark"
    / "fixtures"
    / "agentic_benchmark"
    / "cases"
)
BACKUP_ROOT = (
    Path(__file__).resolve().parents[1]
    / "polaris"
    / "cells"
    / "llm"
    / "evaluation"
    / "fixtures"
    / "agentic_benchmark"
    / "cases_backup"
)


def migrate_case(old_case: dict) -> dict:
    """将旧格式 case 转换为新格式"""
    new_case = old_case.copy()

    # 添加新字段
    new_case["expected_evidence_path"] = old_case.get("expected_evidence_path", [])
    new_case["expected_answer_shape"] = old_case.get("expected_answer_shape", "answer")
    new_case["budget_conditions"] = old_case.get(
        "budget_conditions",
        {
            "max_tokens": 200000,
            "max_turns": 10,
            "max_wall_time_seconds": 300.0,
        },
    )
    new_case["canonical_profile"] = old_case.get("canonical_profile", "canonical_balanced")

    # 转换 judge 字段 - 添加 mode 字段
    old_judge = old_case.get("judge", {})
    new_judge = old_judge.copy()
    new_judge["mode"] = old_judge.get("mode", "agentic")
    new_case["judge"] = new_judge

    return new_case


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移 Benchmark Case JSON 格式")
    parser.add_argument("--dry-run", action="store_true", help="仅显示将要做的更改")
    parser.add_argument("--execute", action="store_true", help="执行迁移")
    args = parser.parse_args()

    if not args.dry_run and not args.execute:
        parser.error("请指定 --dry-run 或 --execute")

    # 创建输出目录
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    # 列出所有 case 文件
    case_files = list(CASES_ROOT.glob("*.json"))
    print(f"找到 {len(case_files)} 个 case 文件")

    for case_file in case_files:
        with open(case_file, encoding="utf-8") as f:
            old_case = json.load(f)

        new_case = migrate_case(old_case)

        # 计算差异
        old_keys = set(old_case.keys())
        new_keys = set(new_case.keys())
        added_keys = new_keys - old_keys

        if added_keys:
            print(f"\n{case_file.name}: 新增字段 {sorted(added_keys)}")

        if args.dry_run:
            continue

        # 备份原始文件
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        shutil.copy2(case_file, BACKUP_ROOT / case_file.name)

        # 写入新文件
        output_file = OUTPUT_ROOT / case_file.name
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(new_case, f, ensure_ascii=False, indent=2)
        print(f"  已迁移: {case_file.name} -> {output_file}")


if __name__ == "__main__":
    main()
