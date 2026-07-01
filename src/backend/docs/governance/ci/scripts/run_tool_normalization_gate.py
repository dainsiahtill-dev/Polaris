#!/usr/bin/env python3
"""Tool Normalization 同步门禁。

检查 contracts.py 与 TOOL_NORMALIZERS 是否同步。
用法:
    python docs/governance/ci/scripts/run_tool_normalization_gate.py --workspace . --mode sync-check
"""

import argparse
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Tool Normalization 同步门禁")
    parser.add_argument("--workspace", default=".", help="仓库根目录")
    parser.add_argument("--mode", default="sync-check", choices=["sync-check", "generate"])
    parser.add_argument("--report", help="输出报告路径 (JSON)")
    args = parser.parse_args()

    # 确保 backend 在 path 中
    src_backend = Path(args.workspace) / "src" / "backend"
    if src_backend.exists() and str(src_backend) not in sys.path:
        sys.path.insert(0, str(src_backend))

    from polaris.kernelone.llm.toolkit.tool_normalization.normalizers import TOOL_NORMALIZERS
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    tool_specs = ToolSpecRegistry.get_all_specs()
    declared = set(tool_specs.keys())
    registered = set(TOOL_NORMALIZERS.keys())

    # 交集：既在 ToolSpecRegistry 又在显式 TOOL_NORMALIZERS
    in_both = declared & registered
    # schema 驱动：ToolSpecRegistry 中存在，未注册显式 normalizer，走 canonical fallback
    schema_driven = declared - registered
    # 孤儿：TOOL_NORMALIZERS 有但 ToolSpecRegistry 没有（遗留注册）
    orphaned = registered - declared

    print(f"ToolSpecRegistry 声明工具数: {len(declared)}")
    print(f"显式 TOOL_NORMALIZERS 注册数: {len(registered)}")
    print(f"显式 normalizer 覆盖: {len(in_both)}")
    print(f"schema-driven fallback 覆盖: {len(schema_driven)}")
    print(f"遗留注册 (orphaned): {len(orphaned)}")

    if schema_driven:
        print("\n[PASS] 以下工具由 schema-driven fallback 规范化:")
        for t in sorted(schema_driven):
            spec = tool_specs.get(t, {})
            aliases = spec.get("arg_aliases", {})
            print(f"  - {t}: arg_aliases={aliases}")
    else:
        print("\n[PASS] 所有 ToolSpecRegistry 工具均有显式 normalizer 注册")

    if orphaned:
        print("\n[WARN] 以下工具在 TOOL_NORMALIZERS 中注册但 ToolSpecRegistry 中无声明 (legacy):")
        for t in sorted(orphaned):
            print(f"  - {t}")

    # 检查 arg_aliases 处理；同时覆盖显式 normalizer 与 schema fallback。
    print("\n检查 arg_aliases 处理:")
    issues = []
    for tool_name in sorted(declared):
        spec = tool_specs.get(tool_name, {})
        aliases = spec.get("arg_aliases", {})
        if not aliases:
            continue
        # 测试：给每个别名传入值，验证是否被正确归一化
        try:
            from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments

            test_args = {alias: f"test_{alias}" for alias in aliases}
            result = normalize_tool_arguments(tool_name, test_args)
            for alias, canonical in aliases.items():
                if alias == canonical:
                    continue
                # 检查是否 alias 被消费（不在 result 中）
                if alias in result:
                    issues.append(f"  {tool_name}: alias '{alias}' not consumed (still in result)")
        except (ImportError, RuntimeError, ValueError) as e:
            issues.append(f"  {tool_name}: normalizer raised {e}")

    if issues:
        print("[FAIL] arg_aliases 处理异常:")
        for issue in issues:
            print(issue)
    else:
        print("[PASS] 所有 arg_aliases 均被正常消费")

    # 生成报告
    if args.report:
        import json

        report = {
            "declared": sorted(declared),
            "registered": sorted(registered),
            "in_both": sorted(in_both),
            "schema_driven": sorted(schema_driven),
            "orphaned": sorted(orphaned),
            "arg_alias_issues": issues,
            "exit_code": 1 if issues else 0,
        }
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\n报告已写入: {args.report}")

    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
