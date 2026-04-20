# VC-20260328: Benchmark Context Adapter Metrics 集成验证卡片

**验证日期**: 2026-03-28
**任务**: ADR-0067 - Context adapter 与 metrics.py 集成
**状态**: 🔴 待执行

---

## 1. 验证目标

确保 `ContextBenchmarkAdapter` 真实调用 `infrastructure/accel/eval/metrics.py` 的指标计算函数，而非返回 stub 零值。

---

## 2. 验证清单

### 2.1 代码实现验证

| # | 检查项 | 预期结果 | 验证方式 |
|---|--------|----------|----------|
| 1 | `context_adapter.py` 不含 `return {"score": 0.0}` 硬编码 | True | `grep -n '"score": 0.0' context_adapter.py` 应无输出 |
| 2 | `from polaris.infrastructure.accel.eval.metrics import` 存在 | True | `grep "from.*accel.eval.metrics"` |
| 3 | `recall_at_k` 被调用 | True | `grep "recall_at_k"` |
| 4 | `reciprocal_rank` 被调用 | True | `grep "reciprocal_rank"` |
| 5 | `ContextCompilerProtocol` DI 注入实现 | True | 代码审查 |

### 2.2 类型安全验证

| # | 检查项 | 预期结果 | 验证方式 |
|---|--------|----------|----------|
| 6 | mypy strict 通过 | 0 errors | `mypy context_adapter.py --strict` |
| 7 | 无 `# type: ignore` 滥用 | <= 1 | 代码审查 |
| 8 | 所有函数有完整类型提示 | True | 代码审查 |

### 2.3 测试验证

| # | 检查项 | 预期结果 | 验证方式 |
|---|--------|----------|----------|
| 9 | `test_context_adapter.py` 存在 | True | `ls test_context_adapter.py` |
| 10 | 测试用例覆盖 recall 计算 | True | `grep -c "recall" test_context_adapter.py` >= 3 |
| 11 | 测试用例覆盖 mrr 计算 | True | `grep -c "mrr" test_context_adapter.py` >= 2 |
| 12 | 测试用例覆盖错误处理 | True | `grep -c "ContextCompilationError" test_context_adapter.py` >= 1 |
| 13 | 所有测试通过 | 100% pass | `pytest test_context_adapter.py -v` |

### 2.4 Fixture 映射验证

| # | 检查项 | 预期结果 | 验证方式 |
|---|--------|----------|----------|
| 14 | `context_fixture_mapper.py` 存在 | True | `ls context_fixture_mapper.py` |
| 15 | 5 个 legacy fixture 可映射 | 5/5 pass | `pytest test_context_fixture_mapper.py -v` |
| 16 | `__init__.py` 显式导出 metrics | True | `grep "__all__" infrastructure/accel/eval/__init__.py` |

### 2.5 集成验证

| # | 检查项 | 预期结果 | 验证方式 |
|---|--------|----------|----------|
| 17 | benchmark 套件测试通过 | >= 70 tests | `pytest polaris/kernelone/benchmark/tests/ -v` |
| 18 | ruff check 通过 | 0 errors | `ruff check polaris/kernelone/benchmark/` |
| 19 | ruff format 通过 | 0 diffs | `ruff format --check polaris/kernelone/benchmark/` |

---

## 3. 验证命令脚本

```bash
#!/bin/bash
# verify_context_adapter.sh

set -e

echo "=== VC-20260328 验证开始 ==="

cd polaris/kernelone/benchmark/adapters

# 1. 检查无硬编码零值
echo "[1/19] 检查无硬编码零值..."
if grep -n '"score": 0.0' context_adapter.py; then
    echo "FAIL: 发现硬编码零值"
    exit 1
fi
echo "PASS"

# 2. 检查 metrics import
echo "[2/19] 检查 metrics 导入..."
grep -q "from.*infrastructure.accel.eval.metrics" context_adapter.py || {
    echo "FAIL: 未导入 metrics"
    exit 1
}
echo "PASS"

# 3. mypy 检查
echo "[3/19] mypy strict 检查..."
mypy context_adapter.py --strict || {
    echo "FAIL: mypy 有误"
    exit 1
}
echo "PASS"

# 4. 运行测试
echo "[4/19] 运行 Context adapter 测试..."
pytest test_context_adapter.py -v || {
    echo "FAIL: 测试失败"
    exit 1
}
echo "PASS"

echo "=== VC-20260328 验证完成 ==="
```

---

## 4. 验证负责人

| 角色 | 专家 | 验证职责 |
|------|------|----------|
| @QA Automation | QA专家 | 执行测试验证 |
| @Typing Specialist | 类型专家 | mypy 检查 |
| @Code Auditor | 审计员 | 代码审查 |
| @Principal Engineer | 架构师 | 最终确认 |

---

## 5. 验证状态跟踪

| 日期 | 状态 | 验证者 | 备注 |
|------|------|--------|------|
| 2026-03-28 | 🔴 待执行 | — | 执行计划已创建 |
| — | ⏳ 执行中 | — | — |
| — | ✅ 完成 | — | — |
