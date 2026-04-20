# M0.3 治理脚本现状测试报告

> 测试日期: 2026/04/13
> 目标目录: `docs/governance/ci/scripts/`

## 1. Gate 脚本列表

| 脚本 | 状态 |
|------|------|
| `run_contextos_governance_gate.py` | **FAIL** |
| `run_cognitive_life_form_gate.py` | **PASS** ✅ |
| `run_kernelone_release_gate.py` | **FAIL** |

## 2. 测试结果详情

### 2.1 run_contextos_governance_gate.py - FAIL

```
[contextos-governance-gate] FAILED
  - ProviderFormatter: ProviderFormatter class not found in llm_caller.py
  - EpisodeCard: PASS
  - SSOTConstraintTests: FAILED (No module named pytest)
```

### 2.2 run_cognitive_life_form_gate.py - PASS ✅

```
{
  "gate": "cognitive_life_form",
  "mode": "all",
  "total_checks": 7,
  "passed": 7,
  "failed": 0
}
```

### 2.3 run_kernelone_release_gate.py - FAIL

```
[kernelone-release-gate] stage=tests failed rc=1
- 86 tests collected, 17 failed
- 主要失败: FileNotFoundError, ModuleNotFoundError, NEW_DIRECT_WRITE_FILE regressions
```

## 3. 失败原因分类统计

| 类别 | 根因 | 涉及脚本 | 优先级 |
|------|------|----------|--------|
| **路径漂移 (AST)** | ProviderFormatter 已移至 `llm_caller/provider_formatter.py` | `run_contextos_governance_gate.py` | P1 |
| **模块导入错误** | `polaris.kernelone.agent.subagent_runtime` 不存在 | `run_kernelone_release_gate.py` | P1 |
| **子进程环境** | pytest 未安装 | `run_contextos_governance_gate.py` | P2 |
| **路径漂移 (目录)** | `polaris/kernelone/tools/__init__.py` 不存在 | `run_kernelone_release_gate.py` | P1 |
| **Baseline 回归** | KFS direct-write baseline 未更新 (39个新文件) | `run_kernelone_release_gate.py` | P2 |
| **超时配置** | Subagent LLM call timeout 为 0.0s | `run_kernelone_release_gate.py` | P3 |

## 4. 根因分析

### 4.1 ProviderFormatter AST 检测失败
- **预期路径**: `polaris/cells/roles/kernel/internal/llm_caller.py`
- **实际位置**: `polaris/cells/roles/kernel/internal/llm_caller/provider_formatter.py`
- **修复**: 更新 AST 搜索路径为 `llm_caller/` 包目录

### 4.2 subagent_runtime 导入路径错误
- **预期**: `polaris.kernelone.agent.subagent_runtime`
- **实际**: `polaris.kernelone.single_agent.subagent_runtime`
- **修复**: 更新测试文件中的导入路径

### 4.3 KFS Baseline 回归
- 39 个新文件写入未被 baseline 登记
- **修复**: 重新生成 `kfs_direct_write_baseline.txt`

## 5. 修复优先级

| 优先级 | 问题 | 修复方案 | 工作量 |
|--------|------|----------|--------|
| P1 | ProviderFormatter AST 路径 | 扫描 `llm_caller/` 包 | 30min |
| P1 | subagent_runtime 导入路径 | 更新导入路径 | 15min |
| P1 | kernelone/tools 路径 | 删除/修正测试检查 | 10min |
| P2 | pytest 未安装 | 添加 pytest 依赖 | 5min |
| P2 | KFS baseline 过期 | 重新生成 baseline | 20min |

## 6. 总结

| 指标 | 数值 |
|------|------|
| 总脚本数 | 3 |
| 通过 | 1 |
| 失败 | 2 |
| P1 阻塞问题 | 3 |
| P2 重要问题 | 2 |

**M0.3 治理脚本就绪度**: 33% (1/3 通过)
