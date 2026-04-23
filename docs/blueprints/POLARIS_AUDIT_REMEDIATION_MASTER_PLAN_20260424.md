# Polaris 审计整改全量落地执行蓝图

**文档编号**: BP-20260424-AUDIT-REMEDIATION-MASTER
**状态**: ACTIVE
**负责人**: Principal Architect
**基线审计**: Polaris 深度审计报告 2026-04-24

---

## 1. 背景与目标

本蓝图基于 2026-04-24 深度审计发现，系统性修复 Polaris 项目三大结构性缺陷：

1. **数据幻觉**: 核心模块 `roles.kernel` 内部覆盖率仅 7.4%，17 个 LLM Provider 零测试，32 个 HTTP Router 零测试
2. **术语通胀**: 三套隐喻系统（官职/生物学/自创术语）叠加，同一实体多命名
3. **结构机械复制**: 8 层目录嵌套，150+ 空目录，80+ 蓝图文件无索引

### 核心目标

将 Polaris 从 **"叙事驱动（Narrative-Driven）"** 转向 **"工程驱动（Engineering-Driven）"**，建立可验证、可度量、可持续的工程基线。

---

## 2. 系统架构图（文本描述）

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    POLARIS AUDIT REMEDIATION SYSTEM                     │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │
│  │  SQUAD A    │    │  SQUAD B    │    │  SQUAD C    │               │
│  │ Test Infra  │    │ Provider    │    │ Router      │               │
│  │ Restoration │    │ Integration │    │ Contract    │               │
│  └──────┬──────┘    └──────┬──────┘    └──────┬──────┘               │
│         │                  │                  │                        │
│         ▼                  ▼                  ▼                        │
│  ┌─────────────────────────────────────────────────────┐              │
│  │           REMEDIATION CORE: pytest-cov               │              │
│  │  (真实覆盖率报告 + 基线对比 + CI 徽章)                │              │
│  └─────────────────────────────────────────────────────┘              │
│         │                  │                  │                        │
│         ▼                  ▼                  ▼                        │
│  ┌─────────────────────────────────────────────────────┐              │
│  │        REMEDIATION CORE: ruff + mypy --strict       │              │
│  │  (代码规范门禁 + 类型安全门禁 + 零裸 except 检查)      │              │
│  └─────────────────────────────────────────────────────┘              │
│         │                  │                  │                        │
│         ▼                  ▼                  ▼                        │
│  ┌─────────────────────────────────────────────────────┐              │
│  │         REMEDIATION CORE: TERMINOLOGY GATE          │              │
│  │  (术语白名单 + 隐喻映射表 + docstring 规范检查)       │              │
│  └─────────────────────────────────────────────────────┘              │
│                                                                         │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐               │
│  │  SQUAD D    │    │  SQUAD E    │    │  SQUAD F    │               │
│  │ Terminology │    │ Blueprint   │    │ Directory   │               │
│  │ Cleanup     │    │ Governance  │    │ Flattening  │               │
│  │ (P1 Batch 1)│    │ (Index +    │    │ (P1 Batch 1)│               │
│  │             │    │  Archive)   │    │             │               │
│  └─────────────┘    └─────────────┘    └─────────────┘               │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 3. 模块职责划分

### Squad A: 测试基础设施修复 (Test Infra Restoration)
**职责**: 移除 `pytest.ini` 中掩盖缺口的排除配置，建立真实覆盖率基线
**输入**: `src/backend/pytest.ini`, `pyproject.toml`
**输出**: 修复后的 `pytest.ini`, 首份真实覆盖率 HTML 报告, CI 徽章配置
**关键约束**:
- 不能简单移除所有 `norecursedirs` —— 需区分"确实废弃"与"被错误排除"
- `delivery/` 目录的排除必须移除（含大量活跃代码）
- `tests/agent_stress/` 如确实有特殊依赖，改为 `pytest -m` marker 排除而非目录排除

### Squad B: LLM Provider 集成测试 (Provider Integration Tests)
**职责**: 为 `polaris/infrastructure/llm/providers/` 下 17 个 provider 编写最小集成测试
**输入**: provider 源码, `provider_registry.py`, `_shared.py`
**输出**: `tests/integration/llm/providers/test_*.py`
**关键约束**:
- 不求 100% mock —— 使用录制/回放（VCR.py 或手工 fixture）模式
- 每个 provider 至少覆盖: `create_completion()` 正常路径、错误路径、参数校验
- 利用 `provider_registry.py` 做统一入口测试，减少重复

### Squad C: HTTP Router 契约测试 (Router Contract Tests)
**职责**: 为 `polaris/delivery/http/routers/` 下 32 个未测 router 编写契约测试
**输入**: router 源码, FastAPI `app` 工厂
**输出**: `tests/integration/delivery/routers/test_*.py`
**关键约束**:
- 使用 `TestClient` 做 HTTP 级契约测试，不深入业务逻辑
- 每个 router 至少覆盖: 200 OK 路径、422 校验失败、401/403 鉴权失败
- 优先覆盖已有测试的 5 个 router 之外的 32 个

### Squad D: 术语清理第一批 (Terminology Cleanup Batch 1)
**职责**: 清理 `polaris/cells/roles/kernel/` 内部代码注释中的隐喻别名
**输入**: kernel 内部模块源码
**输出**: 清理后的源码（保留工程名，移除生物学/官职别名）
**关键约束**:
- 不改动公开 API 名称（避免 breaking change）
- 注释中首次出现隐喻时，保留 "工程名 (原隐喻名)" 格式一次
- 类级 docstring 必须增加工程职责说明，替代隐喻描述

### Squad E: 蓝图治理索引 (Blueprint Governance Index)
**职责**: 为 `docs/blueprints/` 建立索引与归档机制
**输入**: 现有 156 个蓝图文件
**输出**: `docs/blueprints/README.md`, `docs/blueprints/archive/`, 命名规范文档
**关键约束**:
- 按主题分组（ContextOS, TransactionKernel, AgentInstruction, etc.）
- 每组标注: 当前权威蓝图、历史归档、待评估状态
- 统一命名: `{主题}_{日期}_{状态}.md`

### Squad F: 目录结构扁平化第一批 (Directory Flattening Batch 1)
**职责**: 评估并移除 `polaris/cells/` 下无实际内容的 `generated/` / `public/` 空目录
**输入**: cells 目录树
**输出**: 清理后的目录结构, `cell_layout_refactor_plan.md`
**关键约束**:
- 不移动含代码的目录
- 仅移除完全为空的 `generated/` / `public/` / `tests/`（无文件）
- 记录哪些 Cell 的哪些子目录是空的，作为后续架构决策依据

---

## 4. 核心数据流

### 整改数据流（从现状到目标态）

```
阶段 0: 现状基线捕获
┌──────────────────────────────────────────────────────────────┐
│  pytest --collect-only -q → 11860 (虚假)                     │
│  pytest --cov (当前配置) → 掩盖 delivery/ 等目录              │
│  mypy --strict → 排除 infrastructure/, scripts/              │
│  ruff check → ANN 规则全部忽略                               │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
阶段 1: 基础设施修复 (Squad A)
┌──────────────────────────────────────────────────────────────┐
│  1. 修复 pytest.ini norecursedirs                            │
│  2. 运行 pytest --cov → 生成真实覆盖率报告                   │
│  3. 更新 pyproject.toml mypy exclude（逐步缩小）             │
│  4. 启用 ruff ANN 规则（分批）                               │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
阶段 2: 安全网铺设 (Squad B + C)
┌──────────────────────────────────────────────────────────────┐
│  1. Provider 集成测试 → 覆盖 17 个 provider                   │
│  2. Router 契约测试 → 覆盖 32 个 router                       │
│  3. 运行全量测试 → 验证新增测试不破坏现有                     │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
阶段 3: 叙事降噪 (Squad D + E + F)
┌──────────────────────────────────────────────────────────────┐
│  1. 术语清理 → 代码注释去隐喻化                               │
│  2. 蓝图索引 → 156 个文件分类归档                             │
│  3. 目录瘦身 → 移除空目录层                                   │
│  4. 三份文档指标统一 → AGENTS.md / CLAUDE.md / README        │
└──────────────────────┬───────────────────────────────────────┘
                       ▼
阶段 4: 持续度量 (CI Integration)
┌──────────────────────────────────────────────────────────────┐
│  1. CI 徽章: pytest-cov badge, ruff badge, mypy badge        │
│  2. 覆盖率门禁: PR 合并前必须 ≥ 当前基线                     │
│  3. Fitness Rules 状态迁移: draft → enforced                 │
└──────────────────────────────────────────────────────────────┘
```

---

## 5. 技术选型理由

| 技术/工具 | 选型理由 | 替代方案排除 |
|-----------|---------|-------------|
| **pytest + pytest-cov** | 项目已配置，只需修复使用方式 | 无需迁移到 unittest |
| **pytest-asyncio** | 项目大量使用 async/await，已配置 | 无需变更 |
| **httpx.TestClient** | FastAPI 原生支持，适合 Router 契约测试 | 无需引入外部 HTTP 客户端 |
| **VCR.py / 手工 fixture** | Provider 测试需要录制/回放避免真实 API 调用 | 不使用真实 API key（成本+不稳定） |
| **ruff (现有)** | 已配置，只需启用 ANN 规则 | 无需迁移到 flake8 |
| **mypy --strict (现有)** | 已配置 strict，只需缩小 exclude 范围 | 无需迁移到 pyright |
| **pre-commit (现有)** | `pyproject.toml` 已声明，建议激活 | 无 |

---

## 6. 验收标准 (Definition of Done)

### Squad A 验收
- [ ] `pytest.ini` 中 `delivery/` 排除已移除
- [ ] `pytest --cov=polaris --cov-report=term-missing` 可执行并通过
- [ ] 首份真实覆盖率 HTML 报告生成到 `htmlcov/`
- [ ] README 中 `11860+` collect-only 指标已替换为真实通过率徽章

### Squad B 验收
- [ ] 17 个 provider 每个至少 1 个集成测试
- [ ] 测试覆盖 `create_completion` 正常路径 + 错误路径
- [ ] 测试可在无真实 API key 环境下运行（录制/回放或 mock）

### Squad C 验收
- [ ] 32 个未测 router 每个至少 1 个契约测试
- [ ] 测试覆盖 200 OK + 422 Validation Error 路径
- [ ] 全部 router 测试通过 `pytest tests/integration/delivery/routers/`

### Squad D 验收
- [ ] `roles.kernel/internal/` 核心文件注释中的隐喻别名已清理
- [ ] `TurnTransactionController` 类级 docstring 已补充工程职责说明
- [ ] `StreamShadowEngine` 类级 docstring 已补充工程职责说明
- [ ] 无 breaking change（公开 API 名称未改动）

### Squad E 验收
- [ ] `docs/blueprints/README.md` 索引文件存在且可导航
- [ ] 至少 30% 的过期蓝图已移至 `docs/blueprints/archive/`
- [ ] 新命名规范文档 `docs/blueprints/NAMING_CONVENTION.md` 存在

### Squad F 验收
- [ ] 空 `generated/` / `public/` 目录已移除（不含代码的）
- [ ] `cell_layout_refactor_plan.md` 记录每个 Cell 的目录现状
- [ ] 无代码文件被误删

---

## 7. 风险与回滚策略

| 风险 | 概率 | 影响 | 缓解措施 |
|------|------|------|---------|
| 移除 `delivery/` 排除后 pytest 大量失败 | 高 | 中 | 分批移除：先运行看失败数，再决定是修测试还是保留部分排除 |
| Provider 测试依赖真实 API | 中 | 高 | 强制使用录制/回放，CI 中禁止真实调用 |
| 术语清理引入 breaking change | 低 | 高 | 仅改注释不改 API，CI 中跑 `pytest --collect-only` 检测 |
| 蓝图归档误删活跃文档 | 低 | 中 | 归档操作走 git mv，保留历史，可回滚 |
| 多 Squad 并行冲突 | 中 | 中 | Squad A 先完成并合并，B/C/D/E/F 基于新基线并行 |

---

## 8. 执行顺序

```
Week 1 (Squad A 优先):
  Day 1-2: Squad A 修复 pytest.ini，跑真实覆盖率
  Day 3-4: Squad A 生成基线报告，更新 README 指标
  Day 5:   Squad A 合并，成为后续 Squad 基线

Week 2 (Squad B + C + D 并行):
  Day 1-3: Squad B Provider 集成测试
  Day 1-3: Squad C Router 契约测试
  Day 1-3: Squad D 术语清理第一批
  Day 4-5: 全量回归测试，修复冲突

Week 3 (Squad E + F 并行):
  Day 1-3: Squad E 蓝图索引与归档
  Day 1-3: Squad F 目录瘦身
  Day 4-5: 整合、文档统一、最终回归
```

---

**本蓝图为整改项目最高权威文档，所有 Squad 执行以此为准。**
