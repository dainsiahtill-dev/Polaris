# 2026-03-23 审计报告

> 生成时间：2026-03-23
> 快照：pytest --collect-only -q（2026-03-23）
> 测试收集：2495 collected / 0 errors

---

## 1. 已完成任务

### P0 级
| 任务 | 状态 | 摘要 |
|------|------|------|
| P0-1 | ✅ 完成 | embedded_api.py eval() 安全注入漏洞修复 |
| P0-2 | ✅ 完成 | LLM provider 同步 HTTP 阻塞修复（4 个 Provider 全部覆盖） |

### P1 级
| 任务 | 状态 | 摘要 |
|------|------|------|
| P1-1 | ✅ 完成 | 认证遗漏 + 信息泄露 + 权限伪造修复，`require_permission` 重构 |

### P2 级
| 任务 | 状态 | 摘要 |
|------|------|------|
| P2-1 | ✅ 完成 | 异步反模式 + NATS 配置 + time.sleep → asyncio.sleep |
| P2-2 | ✅ 完成 | AGENTS.md 快照更新 + CLAUDE.md 同步 + migration ledger 创建 |

---

## 2. 进行中任务

| 任务 | Owner | 状态 | 摘要 |
|------|-------|------|------|
| P0-3 | code-quality-engineer | 🔄 进行中 | God Object 拆分 + 异常吞噬修复（目标 206 → ≤50） |
| P0-4 | architecture-engineer | 🔄 进行中 | delivery/ 层 Cell internal/ 边界越界修复（49 处，已处理高优先级） |
| P0-5 | test-engineer | 🔄 进行中 | 测试补强收尾 |
| P1-2 | — | 🔄 进行中 | 清除 shim 目录 + 修复 bootstrap 边界越界 |

---

## 3. 测试基线

| 指标 | 2026-03-22 | 2026-03-23 | 变化 |
|------|------------|------------|------|
| 测试收集 | 1643 collected / 12 errors | 2495 collected / 0 errors | +852 collected，errors 清零 |
| `except Exception` | 213 | 206 | -7 |
| bare `pass` | 56 | 53 | -3 |

---

## 4. 高优先级越界违规（待 P0-4 关闭）

以下 internal/ 导入尚未通过 Cell public 契约修复：

### bootstrap/assembly.py
- `from polaris.cells.director.execution.internal.task_lifecycle_service`
- `from polaris.cells.director.execution.internal.worker_pool_service`
- `from polaris.cells.audit.evidence.internal.task_audit_llm_binding`

### delivery/http/v2/resident.py
- `from polaris.cells.resident.autonomy.internal.resident_models`
- `from polaris.cells.resident.autonomy.internal.resident_runtime_service`

### delivery/http/v2/observability.py
- `from polaris.cells.orchestration.workflow_runtime.internal.observability`

### delivery/ws/runtime_endpoint.py
- `from polaris.cells.runtime.projection.internal.status_snapshot_builder`

---

## 5. 环境变量前缀状态

| 前缀 | 总出现 | polaris/ | tests/ | 唯一变量名 |
|------|--------|----------|--------|-----------|
| `POLARIS_` | 746 | 483 | 172 | 249 |
| `KERNELONE_` | 159 | 143 | 9 | 60 |

**策略**：新增/修改时顺带迁移为 `KERNELONE_` 前缀，不大批量重命名。

---

## 6. 已知剩余风险

| 风险 | 影响 | 建议 |
|------|------|------|
| `except Exception` 仍有 206 处 | 异常吞噬风险 | P0-3 继续专项治理，目标 ≤50 |
| 越界 internal/ 导入未全部关闭 | Cell 边界腐蚀 | P0-4 应优先修复上述 7 处高优先级 |
| `test_role_adapters_taskboard_alignment.py` 4 个失败 | 行为一致性存疑 | 需确认是测试问题还是实现问题 |
| 137 个 tracked 文件已修改 | 回归风险 | 需完整测试验证基线 |
| `fix_silent_exceptions.py` 已删除 | 无 | 已清理 |

---

## 7. 未纳入版本控制文件

| 文件 | 来源 | 建议 |
|------|------|------|
| `docs/migration/ledger.yaml` | P2-2 交付物 | 纳入版本控制 |
| `.gitattributes` | 建议新增 | 由 infrastructure-engineer 决定 |
| `refactor_seq_config.py` | 临时脚本 | 评估后删除 |
| `tests/test_cell_di_chain.py` | 测试新增 | 纳入版本控制 |
| `tests/test_director_toolchain_behavior.py` | 测试新增 | 纳入版本控制 |
| `tests/test_pm_orchestration_behavior.py` | 测试新增 | 纳入版本控制 |
