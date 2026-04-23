# Polaris 后端代码修正版收敛计划
**生成日期**: 2026-04-06
**数据来源**: 10专家并行验证 + 基线扫描
**准确性**: 基于实测数据，非过时报告

---

## 一、修正后的关键指标

| 指标 | 原报告声称 | 修正后实际 | 健康度 |
|------|-----------|-----------|--------|
| Descriptor覆盖率 | 0/52 (0%) | **53/53 (100%)** | 🟢 已解决 |
| Cell注册完整性 | 6个未登记 | **100%已登记** | 🟢 已解决 |
| WorkflowEngine重复 | 3处 | **1基类+1子类** | 🟢 可接受 |
| HandlerRegistry重复 | 8+处 | **1协议+1实现** | 🟢 可接受 |
| BudgetPolicy重复 | 4+处 | **3处** | 🟡 需收敛 |
| `except Exception:` | 206处 | **406处** | 🔴 严重 |
| bare `except:` | 53处 | **0处** | 🟢 正常 |
| pytest收集错误 | 11个 | **30个** | 🟡 需修复 |
| KERNELONE_前缀 | 769 | 786 | ⚠️ 需收敛 |
| KERNELONE_前缀 | 225 | 266 | ⚠️ 需收敛 |
| Cell总数 | 52 | **53** | 🟢 正常 |

---

## 二、实际严重问题（按优先级）

### 🔴 P0-1: 异常处理失控（实测406处）

**现状**:
- `except Exception:` 出现 **406次** 分布在 **219个文件**
- 虽然 bare `except:` 为 0（良好），但裸 `except Exception:` 密度过高
- 大量异常被捕获后直接 pass 或仅打印日志不恢复

**根因**:
- 快速迭代期间的"先让它跑起来"代码遗留
- 缺乏统一的异常处理规范执行
- 日志级别不一致（有的 warning 有的 error）

**修复方案**:
```python
# 禁止模式
except Exception:
    pass

# 强制模式（已有部分执行）
except SpecificException as exc:
    logger.warning("context: %s", exc)
    raise  # 或 return_error_result()
```

**执行步骤**:
1. 扫描 406 处，按严重性分类（critical/warning/info）
2. Critical 必须修复（文件系统、网络、超时）
3. Warning 优先修复（业务逻辑、数据转换）
4. Info 延后处理（重试、缓存）
5. 写入 `fitness-rules.yaml` 阻断规则

**预计工时**: 2-3人天

---

### 🔴 P0-2: pytest 收集错误（实测30个）

**现状**: 10,398 测试收集，**30个错误**，主要是 ImportError

**错误分布**:
| 错误类型 | 数量 | 示例 |
|---------|------|------|
| `ModuleNotFoundError` | 11 | `tests/agent_stress/*` |
| `ImportError` | 8 | 各种路径问题 |
| `FileNotFoundError` | 2 | `test_lancedb_store_script.py` |
| 其他 | 9 | 各种收集错误 |

**修复方案**:
```bash
# 1. 修复 agent_stress 路径问题（workspace依赖）
# 2. 修复 import 路径问题
# 3. 清理 .polaris/runtime/ 下的临时测试文件
```

**预计工时**: 1-2人天

---

### 🟡 P1-1: BudgetPolicy 重复（3处）

**现状**: 3个不同实现
1. `polaris/domain/entities/policy.py` - 简单 dataclass
2. `polaris/cells/roles/kernel/internal/policy/budget_policy.py` - 原始类
3. `polaris/cells/roles/kernel/internal/policy/layer/budget.py` - 新版自适应

**修复方案**:
- 保留 `budget_policy.py` 作为核心实现
- `domain/entities/policy.py` 中的 dataclass 改为引用核心实现
- `layer/budget.py` 的自适应功能合并到核心实现

**预计工时**: 3-5人天

---

### 🟡 P1-2: 环境变量前缀收敛

**现状**:
- `KERNELONE_`: 786次/185文件
- `KERNELONE_`: 266次/62文件
- `polaris/kernelone/_runtime_config.py` 已实现 fallback 设计

**理解**: 这是**设计决策**，非 bug：
- `KERNELONE_` 是规范前缀
- `KERNELONE_` 是兼容别名（Polaris 特有）
- bootstrap 层负责映射

**修复方案**:
1. 新代码强制使用 `KERNELONE_`
2. 旧代码 `KERNELONE_` 改为 `KERNELONE_`（内部可保留兼容）
3. 更新 AGENTS.md 文档说明

**预计工时**: 1人天（逐步迁移）

---

## 三、实际良好无需治理的项目

以下项目**不需要修复**（原报告错误声称需要）：

| 项目 | 原报告判定 | 实际情况 |
|------|-----------|---------|
| Descriptor Pack | P0-严重缺失 | ✅ 53/53 完整覆盖 |
| Cell注册 | P0-6个未登记 | ✅ 100%已登记 |
| WorkflowEngine | P0-3处重复 | ✅ 1基类+1子类正常 |
| HandlerRegistry | P0-8+处重复 | ✅ 1协议+1实现正常 |
| bare except | P0-53处 | ✅ Python文件中0处 |

---

## 四、执行计划

### Phase 0: 基线确认（已完成）
- [x] 10专家并行验证
- [x] pytest 收集基线扫描
- [x] 异常处理数量确认
- [x] Cell 注册完整性确认

### Phase 1: 止血（Week 1）

| 任务 | 负责 | 优先级 | 状态 |
|------|------|--------|------|
| pytest 30个错误修复 | TBD | P0 | 待开始 |
| 异常处理分级（406处） | TBD | P0 | 待开始 |

### Phase 2: 核心收敛（Week 2-3）

| 任务 | 负责 | 优先级 | 状态 |
|------|------|--------|------|
| BudgetPolicy 三合一 | TBD | P1 | 待开始 |
| 环境变量前缀文档澄清 | TBD | P1 | 待开始 |

### Phase 3: 长期优化（Week 4+）

| 任务 | 负责 | 优先级 | 状态 |
|------|------|--------|------|
| 406处异常处理修复 | TBD | P0 | 待开始 |
| fitness-rules.yaml 阻断规则完善 | TBD | P1 | 待开始 |

---

## 五、已验证为准确的问题指标

| 问题 | 严重度 | 修正后数值 | 影响 |
|------|--------|-----------|------|
| `except Exception:` | P0 | **406处** | 生产故障难定位 |
| pytest收集错误 | P0 | **30个** | CI可靠性受损 |
| BudgetPolicy重复 | P1 | **3处** | 修改需同步多处 |

---

## 六、不需要修复的项目（原报告误判）

| 项目 | 原报告 | 实测 | 结论 |
|------|--------|------|------|
| Descriptor覆盖率 | 0% | **100%** | 无需修复 |
| Cell注册 | 6个缺失 | **全部** | 无需修复 |
| WorkflowEngine | 3处重复 | **1+1** | 无需修复 |
| HandlerRegistry | 8+重复 | **1+1** | 无需修复 |
| bare except | 53处 | **0处** | 无需修复 |

---

## 七、一句话总结

> **原审计报告存在大量数据失实**。基于实测数据，项目真实健康度远高于报告描述：Descriptor 100%覆盖、Cell 100%注册、核心抽象无重复。**真正需要治理的是 406处异常处理和30个pytest收集错误**，其他"P0问题"均已正常。

---

## 八、后续行动

1. **立即**: 修复 pytest 30个收集错误（1-2人天）
2. **本周**: 对 406处异常处理进行分类分级
3. **下周**: 修复 Critical 级异常处理
4. **持续**: BudgetPolicy 收敛 + 环境变量文档澄清
