# ADR-0069: Benchmark Sandbox Materialization Governance

**状态**: 已接受  
**日期**: 2026-04-08  
**决策者**: Polaris Backend Benchmark Maintainers

---

## 背景

`llm.evaluation` 的 agentic benchmark 与 tool-calling matrix 在 sandbox 物化路径和复制策略上存在不一致：

1. fixture 复制未过滤 `.git`、缓存目录与 `*.pyc`。
2. tool-calling matrix 将 sandbox 写在 workspace 根目录，偏离 `state_owners` 与 `effects_allowed`。
3. 原始 `case_id` 直接入路径，放大 Windows 长路径失败概率。
4. suite 失败时 runner 统计存在 `total_cases=0` 的失真。

这些问题同时影响稳定性、可审计性和治理一致性。

## 决策

### 1) 统一 sandbox 命名与路径

- sandbox 目录名采用 `case_id-prefix + hash`。
- sandbox 根目录统一为：
  - `resolve_runtime_path(workspace, "runtime/llm_evaluations/<run_id>/sandboxes/<sandbox_key>")`

### 2) 统一 fixture 复制忽略规则

复制时忽略：

- `.git`
- `__pycache__`
- `.pytest_cache`
- `.mypy_cache`
- `*.pyc`
- `*.pyo`

### 3) 统一失败统计语义

Runner 对“无 case 细项的 suite 失败”按 1 个聚合 case 计数，避免 `total_cases=0`。

### 4) 统一类型边界

`agentic_benchmark` 在进入 observation 收集前统一规范 `model` 为非空字符串，消除可选类型漂移。

## 后果

### 正面

- 减少路径长度与缓存污染导致的 flaky 失败。
- 与 `llm.evaluation` 的状态拥有边界一致。
- 失败统计可解释且可追踪。
- 关键 benchmark 路径行为在 agentic/matrix 两个套件保持一致。

### 负面

- 旧测试中对固定 sandbox 路径的断言需要更新。
- sandbox 目录名由可读 `case_id` 变为“前缀+hash”，排障时需看元数据或 case payload。

## 验证

- Verification Card: `docs/governance/templates/verification-cards/vc-20260408-benchmark-sandbox-hardening.yaml`
- Regression tests:
  - `tests/test_llm_agentic_benchmark.py`
  - `tests/test_llm_tool_calling_matrix.py`
  - `tests/test_llm_benchmark_loader.py`
  - `polaris/cells/llm/evaluation/tests/test_runner.py`
  - `polaris/kernelone/benchmark/tests/*`

