# Polaris 全量优化执行蓝图 v3.0

**文档类型**: Execution Blueprint  
**日期**: 2026-04-26  
**状态**: In Progress  
**目标**: 零失败测试 + 零隐喻残留 + CI 全绿

---

## 1. 当前基线

- **128 文件已改**: 隐喻清零 + 目录清理 + CI 加固
- **44 核心测试通过**: 角色/审计/集成 green
- **67 预存在失败**: 已识别根因，待修复
- **97 空 scaffold**: 待清理
- **覆盖率**: 23.3%，目标 30%
- **前端构建**: 2 个预存在错误

---

## 2. 全量执行计划

### Wave 1: 测试修复（P0 - 阻断）

**Expert G: Audit Subsystem Fixer**
- 目标: 修复 audit 相关 ~30 个测试失败
- 文件域: `src/backend/polaris/kernelone/audit/`, `src/backend/polaris/tests/audit/`
- 关键修复:
  - Windows 路径绝对化: `evidence_paths.py` 使用 `pathlib.Path`
  - 空字典指纹: `compute_contract_fingerprint` 修复边界条件
  - Schema 优先级: 添加 `"info"` 到有效值列表
  - LLMEvent 长度: 放宽 `prompt_preview` 限制或正确截断
  - 缺失枚举: 添加 `FailureClass.TASK_FAILURE`

**Expert H: Platform Compatibility Engineer**
- 目标: 修复平台相关 ~20 个测试失败
- 文件域: `cells/chief_engineer/`, `cells/workspace/`, `infrastructure/`
- 关键修复:
  - `ce_consumer_cli.py`: 添加 `import asyncio`
  - `workspace.integrity.public.service`: 移除 lazy-load 的 `None` 预声明
  - `test_log_store.py`: 使用 `Path` 而非字符串 `/`
  - `test_gate_checker.py`: Windows 兼容 (`cmd /c echo`)
  - circuit breaker: 测试隔离 (teardown 重置状态)

### Wave 2: 工程清理（P1）

**Expert I: Directory Hygiene Specialist**
- 目标: 删除 97 个空 scaffold 目录
- 策略:
  1. 对每个仅含 `__init__.py` 的目录，grep 全仓确认无 import
  2. 安全删除（无引用）
  3. 更新父目录 `__init__.py` 的 re-export
  4. 验证 `pytest --collect-only` 无 import 错误

**Expert J: Coverage Engineer**
- 目标: 覆盖率 23.3% → 30%
- 策略:
  1. 识别 TOP 10 高价值 0% 覆盖模块（public API，频繁导入）
  2. 编写最小测试集（Happy Path + Edge + Error）
  3. 聚焦: HTTP routers, CLI commands, public services
  4. 更新 CI: `--cov-fail-under=30`

### Wave 3: 治理与构建（P2）

**Expert K: Governance Automation Engineer**
- 目标: 接入 5+ 治理脚本到 CI
- 候选脚本:
  - `check_directory_hygiene.py`
  - `check_context_pack_freshness.py`
  - `check_shim_markers.py`
  - `check_legacy_coverage.py`
  - `check_catalog_presence.py`
- 新增 CI job: `governance`（并行执行）
- 生成 `metrics_history/fitness_rules.json`

**Expert L: Build Engineer**
- 目标: 修复构建系统
- 任务:
  1. 找到并运行 `descriptor_pack_generator`
  2. 验证 `generated/` 无隐喻残留
  3. 创建/修复 `PolarisTerminalRenderer.tsx`
  4. 验证 `npm run build` 通过

---

## 3. 验收门禁

```bash
# 1. 隐喻清零
grep -r "尚书令\|中书令\|工部尚书\|工部侍郎\|门下侍中" src/frontend/src src/backend/polaris
# => 0 matches

# 2. 测试全绿
pytest src/backend/polaris/tests/ -q --timeout=60
# => >13,000 collected, <5 failures

# 3. 覆盖率
pytest --cov=polaris --cov-fail-under=30
# => PASS

# 4. 空目录
find src/backend/polaris -type d -empty
# => 0 results

# 5. Ruff
ruff check src/backend/polaris
# => 0 errors

# 6. MyPy
mypy src/backend/polaris/kernelone src/backend/polaris/delivery src/backend/polaris/cells --ignore-missing-imports
# => 0 errors

# 7. 前端构建
cd src/frontend && npm run build
# => PASS
```

---

## 4. 风险管控

| 风险 | 缓解 |
|------|------|
| 测试修复引入新失败 | 每修复一个 rerun 相关测试 |
| 删除空目录破坏 import | grep 验证无引用后再删 |
| 覆盖率测试过慢 | 仅测 public API，mock 底层 |
| 治理脚本失败阻塞 CI | 新增脚本设 `continue-on-error: true` |
| 前端构建依赖缺失 | 创建最小 stub，不新增 npm 包 |

---

*本蓝图指导全量优化最终波次，6 位专家并行执行，完成后统一回归验证。*
