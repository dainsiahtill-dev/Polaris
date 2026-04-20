# Polaris 前端/Electron 开发完善路线图

> **审计日期**: 2026-04-16
> **审计团队**: 多维度技术审计团队（6个子agent）
> **目标**: 使 Polaris 成为世界顶级 AI Agent 自动化软件开发工具

---

## 📊 审计评分总览

| 维度 | 评分 | 状态 |
|------|------|------|
| API 对齐度 | 75% | 🔴 2个致命缺口 |
| UI/UX 完整性 | ★★★☆☆ | 🟡 核心具备，缺协作 |
| Electron 安全 | ★★★★☆ | 🟢 良好，缺 sandbox |
| 状态管理 | ★★★☆☆ | 🟡 混合架构，需统一 |
| 类型安全 | ★★☆☆☆ | 🔴 42处 any 违规 |
| 测试覆盖 | ★★★☆☆ | 🟡 单元82%，缺集成 |

---

## 🔴 P0 - 致命缺口（立即修复）

### Issue #1: Factory API 路径不匹配
| 项目 | 值 |
|------|-----|
| **前端调用** | `/v2/factory/runs` |
| **后端实现** | `/factory/runs` (无 v2 前缀) |
| **影响** | Factory 无法从前端启动 |
| **文件** | `src/backend/polaris/delivery/http/routers/factory.py` |
| **状态** | ⏳ 待修复 |

### Issue #2: EvidenceViewer URL 错误
| 项目 | 值 |
|------|-----|
| **前端调用** | `/api/v2/resident/decisions/.../evidence` |
| **正确路径** | `/v2/resident/decisions/.../evidence` |
| **影响** | 决策证据查看器 404 |
| **文件** | `src/frontend/src/app/components/resident/EvidenceViewer.tsx:62` |
| **状态** | ⏳ 待修复 |

---

## 🟠 P1 - 高优先级缺口

| ID | 问题 | 文件 | 状态 |
|----|------|------|------|
| P1-1 | Conversation API 响应格式不匹配 | `conversationApi.ts` | ⏳ |
| P1-2 | 42处 `any` 违规，违反 CLAUDE.md | 多文件 | ⏳ |
| P1-3 | `sandbox: true` 未启用 | `main.cjs` | ⏳ |
| P1-4 | CSP `unsafe-inline` 风险 | `index.html` | ⏳ |

---

## 🟡 P2 - 中优先级缺口

| ID | 问题 | 建议方案 |
|----|------|----------|
| P2-1 | `useRuntime` 1200行过重 | 按领域拆分 |
| P2-2 | 多种状态管理方案并存 | 统一到 Zustand |
| P2-3 | 无集中缓存层 | 引入 React Query |
| P2-4 | 组件测试覆盖率低 | 补充核心组件测试 |
| P2-5 | 14个 hook 无测试 | 补充 hook 测试 |

---

## 🟢 P3 - 增强功能（长期）

| 功能 | 竞品 | 工期 |
|------|------|------|
| 云端工作区同步 | Cursor AI | 4周 |
| Monaco Editor 集成 | VS Code | 3周 |
| Kanban 看板视图 | Linear | 2周 |
| 系统托盘 + 通知 | 全竞品 | 1周 |
| 主题切换器 | 全竞品 | 1周 |
| 实时协作编辑 | Figma | 6周 |

---

## 📋 实施计划

| Phase | 内容 | 工期 | 负责人 |
|-------|------|------|--------|
| **Phase 1** | P0/P1 致命缺口修复 | 1-2周 | 前端安全专家 |
| **Phase 2** | 架构重构（状态管理+测试） | 3-4周 | 前端架构师 |
| **Phase 3** | 核心功能补全 | 4-6周 | UI/UX专家 |
| **Phase 4** | 世界顶级对标 | 6-8周 | 全栈专家 |

---

## 📁 子蓝图索引

| 文件 | 内容 |
|------|------|
| `frontend/PHASE1_P0_FIXES_202604.md` | P0/P1 致命缺口修复蓝图 |
| `frontend/PHASE2_ARCH_REFACTOR_202604.md` | 架构重构蓝图 |
| `frontend/PHASE3_CORE_FEATURES_202604.md` | 核心功能补全蓝图 |
| `frontend/PHASE4_WORLD_CLASS_202604.md` | 世界顶级对标蓝图 |

---

## ✅ 验收标准

- [ ] Phase 1: API 对齐度 75% → 98%
- [ ] Phase 1: `any` 违规 42处 → 0处
- [ ] Phase 2: 组件测试覆盖率 40% → 80%
- [ ] Phase 3: Electron 安全评分 ★★★★☆ → ★★★★★
- [ ] Phase 4: UI/UX 竞品差距 8项 → <3项
