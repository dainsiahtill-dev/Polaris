# Polaris 测试覆盖率全量提升蓝图

**版本**: v1.0  
**日期**: 2026-04-30  
**目标**: 将测试覆盖率从 14% 提升至 25%+（满足 CI `--cov-fail-under=25` 门禁）  
**范围**: `src/backend/polaris/` 全量 Python 模块  
**团队**: 10名资深Python工程师  
**预计工期**: 48小时（分3个sprint）

---

## 一、现状评估

### 1.1 关键指标
| 指标 | 当前值 | 目标值 | 差距 |
|------|--------|--------|------|
| 测试收集数 | 11,132 | 15,000+ | +3,868 |
| 代码覆盖率 | 14% | 25%+ | +11% |
| 0%覆盖模块 | 1,152 | <500 | -652 |
| CI门禁状态 | ❌ 失败 | ✅ 通过 | 需修复 |
| 治理测试 | 228/228 ✅ | 维持100% | - |
| Ruff规范 | ✅ 通过 | 维持 | - |
| MyPy类型 | ✅ 通过 | 维持 | - |

### 1.2 根因分析
1. **测试债务积累**: 2,732个Python文件中1,152个（42%）完全无测试
2. **优先级的误配**: 前期投入在治理/架构测试（已修复），核心业务逻辑测试不足
3. **模块复杂度差异**: kernelone/（1,068文件）和cells/（1,167文件）占总量83%，但测试覆盖仅12%
4. **Windows环境限制**: asyncio selector超时导致部分HTTP/集成测试无法本地运行

---

## 二、系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    Polaris 测试覆盖提升架构                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │  Sprint 1    │  │  Sprint 2    │  │  Sprint 3    │     │
│  │ (0-16h)      │  │ (16-32h)     │  │ (32-48h)     │     │
│  │ 纯函数/数据类  │  │ 业务逻辑服务  │  │ 集成/边界测试  │     │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘     │
│         │                 │                 │              │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────▼───────┐     │
│  │ kernelone/   │  │ cells/       │  │ delivery/    │     │
│  │ utils,       │  │ orchestration│  │ http, ws     │     │
│  │ contracts    │  │ services     │  │ integration  │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                              │
│  ┌────────────────────────────────────────────────────┐    │
│  │              统一质量门禁层 (Quality Gates)            │    │
│  │  • pytest --cov=polaris --cov-fail-under=25        │    │
│  │  • ruff check . --fix && ruff format .             │    │
│  │  • mypy polaris/ --strict                          │    │
│  └────────────────────────────────────────────────────┘    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 三、模块职责划分

### 3.1 10人团队分工

| 工程师 | 负责领域 | 目标模块数 | 预估测试数 | Sprint |
|--------|----------|------------|------------|--------|
| **工程师A** | kernelone/utils + kernelone/tool_execution | 15 | 150 | 1 |
| **工程师B** | kernelone/context + kernelone/llm/contracts | 15 | 150 | 1 |
| **工程师C** | cells/archive + cells/roles/kernel | 15 | 150 | 1-2 |
| **工程师D** | cells/orchestration + cells/director | 15 | 150 | 1-2 |
| **工程师E** | delivery/cli + delivery/ws | 15 | 150 | 2 |
| **工程师F** | domain/entities + domain/services | 15 | 150 | 2 |
| **工程师G** | domain/verification + domain/state_machine | 15 | 150 | 2 |
| **工程师H** | infrastructure/ + application/ | 15 | 150 | 2-3 |
| **工程师I** | bootstrap/ + 集成测试 | 10 | 100 | 3 |
| **工程师J** | CI/CD加固 + 覆盖率监控 + 回归验证 | - | - | 3 |

### 3.2 执行策略

**Sprint 1（黄金模块期）**
- 聚焦298个"纯函数、无I/O"候选模块
- 预期覆盖提升: 14% → 19%（+5%）
- 产出: 800+新测试

**Sprint 2（业务逻辑期）**
- 补充中等复杂度服务层测试
- Mock外部依赖（数据库、HTTP、文件系统）
- 预期覆盖提升: 19% → 23%（+4%）
- 产出: 600+新测试

**Sprint 3（集成与边界期）**
- 端到端workflow测试
- Windows兼容层测试
- 性能回归测试
- 预期覆盖提升: 23% → 26%（+3%）
- 产出: 400+新测试

---

## 四、核心数据流

```
测试编写流水线:
1. 模块扫描 → 2. 优先级排序 → 3. 测试编写 → 4. 本地验证 → 5. 覆盖率增量确认
     ↑                                                             ↓
     └──────────────── 回归测试（全量pytest） ←─────────────────────┘

质量门禁流水线:
1. ruff check → 2. ruff format → 3. mypy --strict → 4. pytest --cov → 5. 阈值判定
```

---

## 五、技术选型理由

1. **pytest**: 已有pytest基础设施，pytest-cov插件支持行级覆盖率
2. **tmp_path fixture**: 替代mock文件系统，保证测试真实性
3. **pytest-mock**: 统一mock管理，避免unittest.mock样板代码
4. **工厂模式**: 为复杂dataclass创建test factories，减少样板
5. **参数化测试**: `@pytest.mark.parametrize`覆盖边界条件矩阵
6. **并行执行**: `-n auto`（pytest-xdist）加速全量回归

---

## 六、风险与缓解

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|----------|
| Windows async超时阻塞 | 高 | 中 | 标记`@pytest.mark.skipif(sys.platform=='win32')` |
| 模块间循环导入 | 中 | 高 | 使用`TYPE_CHECKING`guard，渐进式import |
| 测试维护成本 | 中 | 中 | 纯函数优先，避免过度mock |
| 覆盖率虚高 | 低 | 高 | 禁止无意义测试（如`assert True`），强制断言业务逻辑 |

---

## 七、验收标准

- [ ] 全量pytest收集0 errors
- [ ] 覆盖率≥25%（pytest-cov行级）
- [ ] ruff check全仓通过
- [ ] mypy `--strict`新增模块0错误
- [ ] CI pipeline全绿
- [ ] 新增测试100%通过，无flaky test

---

## 八、关键文件清单

| 文件路径 | 当前状态 | 目标 |
|----------|----------|------|
| `.github/workflows/ci.yml` | `--cov-fail-under=25` | 维持 |
| `pyproject.toml` | coverage source配置 | 维持 |
| `pytest.ini` | timeout=30s | 维持 |
| `docs/blueprints/COVERAGE_IMPROVEMENT_BLUEPRINT_20260430.md` | 本文档 | 完成 |

---

**Approved by**: Principal Architect  
**Next Step**: 阶段二 - 委派10人工程师团队分Sprint执行
